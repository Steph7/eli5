# -*- coding: utf-8 -*-
from __future__ import absolute_import
import re
from singledispatch import singledispatch
from typing import Any, Dict, List, Tuple

import numpy as np
from xgboost import XGBClassifier, XGBRegressor, Booster, DMatrix

from eli5.base import (
    FeatureWeight, FeatureImportances, Explanation, TargetExplanation)
from eli5.explain import explain_weights, explain_prediction
# FIXME - eli5.sklearn imports do not look good
from eli5.sklearn.explain_prediction import (
    _handle_vec, _get_X, _add_weighted_spans)  # TODO: make public
from eli5.sklearn.utils import get_feature_names, is_probabilistic_classifier
from eli5.utils import argsort_k_largest_positive, get_target_display_names
from eli5._feature_weights import get_top_features


DESCRIPTION_XGBOOST = """
XGBoost feature importances; values are numbers 0 <= x <= 1;
all values sum to 1.
"""


@explain_weights.register(XGBClassifier)
@explain_weights.register(XGBRegressor)
@singledispatch
def explain_weights_xgboost(xgb,
                            vec=None,
                            top=20,
                            target_names=None,  # ignored
                            targets=None,  # ignored
                            feature_names=None,
                            feature_re=None):
    """
    Return an explanation of an XGBoost estimator (via scikit-learn wrapper).
    """
    feature_names = get_feature_names(xgb, vec, feature_names=feature_names)
    coef = xgb.feature_importances_

    if feature_re is not None:
        feature_names, flt_indices = feature_names.filtered_by_re(feature_re)
        coef = coef[flt_indices]

    indices = argsort_k_largest_positive(coef, top)
    names, values = feature_names[indices], coef[indices]
    return Explanation(
        feature_importances=FeatureImportances(
            [FeatureWeight(*x) for x in zip(names, values)],
            remaining=np.count_nonzero(coef) - len(indices),
        ),
        description=DESCRIPTION_XGBOOST,
        estimator=repr(xgb),
        method='feature importances',
    )


@explain_prediction.register(XGBClassifier)
@singledispatch
def explain_prediction_xgboost(
        clf, doc,
        vec=None,
        top=None,
        target_names=None,
        targets=None,
        feature_names=None,
        vectorized=False):
    """ Return an explanation of xgboost prediction.
    """
    vec, feature_names = _handle_vec(clf, doc, vec, vectorized, feature_names)
    if feature_names.bias_name is None:
        # xgboost estimators do not have an intercept, but here we interpret
        # them as having an intercept
        feature_names.bias_name = '<BIAS>'
    X = _get_X(doc, vec=vec, vectorized=vectorized)

    # FIXME copy-paster from eli5.sklearn.explain_prediction
    if is_probabilistic_classifier(clf):
        try:
            proba, = clf.predict_proba(X)
        except NotImplementedError:
            proba = None
    else:
        proba = None

    display_names = get_target_display_names(
        clf.classes_, target_names, targets)

    scores_weights = prediction_feature_weights(clf, X, feature_names)

    # FIXME: again, mostly copy-paste from eli5.sklearn.explain_prediction

    res = Explanation(
        estimator=repr(clf),
        method='decision paths',
        targets=[],
    )
    if clf.n_classes_ > 2:
        for label_id, label in display_names:
            score, feature_weights = scores_weights[label_id]
            target_expl = TargetExplanation(
                target=label,
                feature_weights=get_top_features(
                feature_names, feature_weights, top),
                score=score,
                proba=proba[label_id] if proba is not None else None,
            )
            _add_weighted_spans(doc, vec, vectorized, target_expl)
            res.targets.append(target_expl)
    else:
        (score, feature_weights), = scores_weights
        target_expl = TargetExplanation(
            target=display_names[1][1],
            feature_weights=get_top_features(
                feature_names, feature_weights, top),
            score=score,
            proba=proba[1] if proba is not None else None,
        )
        _add_weighted_spans(doc, vec, vectorized, target_expl)
        res.targets.append(target_expl)

    return res


def prediction_feature_weights(clf, X, feature_names):
    """ For each target, return score and numpy array with feature weights
    on this prediction, following an idea from
    http://blog.datadive.net/interpreting-random-forests/
    """
    # XGBClassifier does not have pred_leaf argument, so use booster
    booster = clf.booster()  # type: Booster
    leaf_ids, = booster.predict(DMatrix(X, missing=clf.missing), pred_leaf=True)
    # TODO - check speed (including indexed_leafs and parse_tree_dump),
    # add an option to pass already prepared trees if it's slow.
    tree_dumps = booster.get_dump()
    assert len(tree_dumps) == len(leaf_ids)
    # For multiclass, xgboost stores dumps and leaf_ids in a 1d array anyway,
    # so we need to split them.
    scores_weights = []
    for start_idx in range(0, len(leaf_ids), clf.n_estimators):
        end_idx = start_idx + clf.n_estimators
        scores_weights.append(
            target_feature_weights(
                leaf_ids[start_idx:end_idx],
                tree_dumps[start_idx:end_idx],
                feature_names,
            ))
    return scores_weights


def target_feature_weights(leaf_ids, tree_dumps, feature_names):
    feature_weights = np.zeros(len(feature_names))
    # All trees in xgboost give equal contribution to the prediction:
    # it is equal to sum of "leaf" values in leafs
    # before applying loss-specific function
    # (e.g. logistic for "binary:logistic" loss).
    score = 0
    for text_dump, leaf_id in zip(tree_dumps, leaf_ids):
        leaf = indexed_leafs(parse_tree_dump(text_dump))[leaf_id]
        score += leaf['leaf']
        path = [leaf]
        while 'parent' in path[-1]:
            path.append(path[-1]['parent'])
        path.reverse()
        # Check how each split changes "leaf" value
        for parent, node in zip(path, path[1:]):
            f_num_match= re.search('^f(\d+)$', parent['split'])
            feature_idx = int(f_num_match.groups()[0]) - 1
            feature_weights[feature_idx] += node['leaf'] - parent['leaf']
        # Root "leaf" value is interpreted as bias
        feature_weights[feature_names.bias_idx] += path[0]['leaf']
    return score, feature_weights


def indexed_leafs(parent):
    """ Return a leaf nodeid -> node dictionary with
    "parent" and "leaf" (average child "leaf" value) added to all nodes.
    """
    indexed = {}
    for child in parent['children']:
        child['parent'] = parent
        if 'leaf' in child:
            indexed[child['nodeid']] = child
        else:
            indexed.update(indexed_leafs(child))
    parent['leaf'] = np.mean([child['leaf'] for child in parent['children']])
    return indexed


def parse_tree_dump(text_dump):
    """ Parse text tree dump (one item of a list returned by Booster.get_dump())
    into json format that will be used by next xgboost release.
    """
    result = None
    stack = []  # type: List[Dict]
    for line in text_dump.split('\n'):
        if line:
            depth, node = _parse_dump_line(line)
            if depth == 0:
                assert not stack
                result = node
                stack.append(node)
            elif depth > len(stack):
                raise ValueError('Unexpected dump structure')
            else:
                if depth < len(stack):
                    stack = stack[:depth]
                stack[-1].setdefault('children', []).append(node)
                stack.append(node)
    return result


def _parse_dump_line(line):
    # type: (str) -> Tuple[int, Dict[str, Any]]
    branch_match = re.match(
        '^(\t*)(\d+):\[(\w+)<([^\]]+)\] yes=(\d+),no=(\d+),missing=(\d+)$', line)
    if branch_match:
        tabs, node_id, feature, condition, yes, no, missing = \
            branch_match.groups()
        depth = len(tabs)
        return depth, {
            'depth': depth,
            'nodeid': int(node_id),
            'split': feature,
            'split_condition': float(condition),
            'yes': int(yes),
            'no': int(no),
            'missing': int(missing),
        }
    leaf_match = re.match('^(\t*)(\d+):leaf=(.*)$', line)
    if leaf_match:
        tabs, node_id, value = leaf_match.groups()
        depth = len(tabs)
        return depth, {
            'nodeid': int(node_id),
            'leaf': float(value),
        }
    raise ValueError('Line in unexpected format: {}'.format(line))
