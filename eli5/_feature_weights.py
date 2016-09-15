# -*- coding: utf-8 -*-
from __future__ import absolute_import
import numpy as np

from .utils import argsort_k_largest, argsort_k_smallest


def get_top_features(feature_names, coef, top):
    """
    Return a ``(pos, neg)`` tuple. ``pos`` and ``neg`` are lists of
    ``(name, value)`` tuples for features with positive and negative
    coefficients.

    Parameters:

    * ``feature_names`` - a vector of feature names;
    * ``coef`` - coefficient vector; coef.shape must be equal to
      feature_names.shape;
    * ``top`` can be either a number or a ``(num_pos, num_neg)`` tuple.
      If ``top`` is a number, ``top`` features with largest absolute
      coefficients are returned. If it is a ``(num_pos, num_neg)`` tuple,
      the function returns no more than ``num_pos`` positive features and
      no more than ``num_neg`` negative features.
    """
    if isinstance(top, (list, tuple)):
        num_pos, num_neg = top
        pos = _get_top_positive_features(feature_names, coef, num_pos)
        neg = _get_top_negative_features(feature_names, coef, num_neg)
    else:
        pos, neg = _get_top_abs_features(feature_names, coef, top)
    return pos, neg


def _get_top_abs_features(feature_names, coef, k):
    indices = argsort_k_largest(np.abs(coef), k)
    features = _features(indices, feature_names, coef)
    return _positive(features), _negative(features)


def _get_top_positive_features(feature_names, coef, k):
    indices = argsort_k_largest(coef, k)
    return _positive(_features(indices, feature_names, coef))


def _get_top_negative_features(feature_names, coef, k):
    indices = argsort_k_smallest(coef, k)
    return _negative(_features(indices, feature_names, coef))


def _positive(features):
    return [(name, value) for (name, value) in features if value > 0]


def _negative(features):
    return [(name, value) for (name, value) in features if value < 0]


def _features(indices, feature_names, coef):
    if not indices.shape[0]:
        return []
    names, values = feature_names[indices], coef[indices]
    return list(zip(names, values))


