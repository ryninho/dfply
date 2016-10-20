from __future__ import absolute_import

from pandas_ply import symbolic
from pandas_ply.symbolic import X
from six.moves import reduce
from six import wraps

import pandas as pd
import numpy as np
import warnings


# Initialize the global X symbol
X(0)

class pipe(object):
    """Generic pipe decorator class that allows DataFrames to be passed
    through the `__rrshift__` binary operator, `>>`

    Adapted from:
    https://github.com/JulienPalard/Pipe/blob/master/pipe.py

    Where the two differences are the `>>` operator is used instead of the
    `|` operator, and DataFrame copying logic occurs in the operator
    overloader function.
    """

    __name__ = "pipe"

    def __init__(self, function):
        self.function = function


    def __rrshift__(self, other):
        other_copy = other.copy()
        other_copy._grouped_by = getattr(other, '_grouped_by', None)
        return self.function(other_copy)


    def __call__(self, *args, **kwargs):
        return pipe(lambda x: self.function(x, *args, **kwargs))



class group_delegation(object):
    """Decorator class that managing grouped operations on DataFrames.

    Checks for an attached `df._grouped_by` attribute added to a
    pandas DataFrame by the `groupby` function.

    If groups are found, the operation defined by the function is
    carried out for each group individually. The internal
    `_apply_combine_reset` function ensures that hierarchical
    indexing is removed.
    """

    __name__ = "group_delegation"

    def __init__(self, function):
        self.function = function


    def _apply_combine_reset(self, grouped, *args, **kwargs):
        combined = grouped.apply(self.function, *args, **kwargs)

        for name in combined.index.names[:-1]:
            if name in combined:
                combined.reset_index(level=0, drop=True, inplace=True)
            else:
                combined.reset_index(level=0, inplace=True)

        if (combined.index == 0).all():
            combined.reset_index(drop=True, inplace=True)

        return combined


    def __call__(self, *args, **kwargs):
        assert (len(args) > 0) and (isinstance(args[0], pd.DataFrame))

        df = args[0]
        grouped_by = getattr(df, "_grouped_by", None)

        if grouped_by is not None:
            df = df.groupby(grouped_by)

            try:
                assert self.function.function.__name__ == 'transmute'
                pass_args = grouped_by
            except:
                pass_args = args[1:]

            df = self._apply_combine_reset(df, *pass_args, **kwargs)
            if all([True if group in df.columns else False for group in grouped_by]):
                df._grouped_by = grouped_by
            else:
                warnings.warn('Grouping lost during transformation.')
            return df

        else:
            return self.function(*args, **kwargs)


class symbolic_evaluation(object):
    """Decorator class that evaluates symbolic arguments and keyword arguments
    passed through to the decorated function.

    The pandas-ply special symbolic representation of the DataFrame is `X`.
    Decorating a function with this decorator will evaluate any arguments or
    keyword arguments that are symbolic pandas objects.
    """

    __name__ = "symbolic_evaluation"


    def __init__(self, function):
        self.function = function


    def _recursive_to_callable(self, df, arg):
        if isinstance(arg, (list, tuple)):
            return [self._recursive_to_callable(df, subarg) for subarg in arg]
        if isinstance(arg, (symbolic.Symbol, symbolic.GetAttr, symbolic.Call)):
            arg = symbolic.to_callable(arg)(df)
        return arg


    def _args_eval(self, df, args):
        return [df]+[self._recursive_to_callable(df, arg) for arg in args]


    def _kwargs_eval(self, df, kwargs):
        return {k:self._recursive_to_callable(df, v) for k,v in kwargs.items()}


    def __call__(self, *args, **kwargs):
        assert (len(args) > 0) and (isinstance(args[0], pd.DataFrame))
        if len(args) > 1:
            args = self._args_eval(args[0], args[1:])
        if len(kwargs) > 0:
            kwargs = self._kwargs_eval(args[0], kwargs)
        return self.function(*args, **kwargs)



class symbolic_reference(object):
    """Decorator class that converts symbolic arguments and keyword arguments
    into their names (typically `pandas.Series` objects).

    This is similar to the `symbolic_evaluation` decorator, but when names or
    labels of the pandas objects are desired over their full evaluated form.
    This decorator is purely for convenience; using `symbolic_evaluation` and
    then manually extracting the labels within the decorated function would
    do the same thing.
    """

    __name__ = "symbolic_reference"


    def __init__(self, function):
        self.function = function


    def _label_or_arg(self, df, arg):
        arg = symbolic.to_callable(arg)(df)
        if isinstance(arg, pd.Series):
            return arg.name
        elif isinstance(arg, pd.DataFrame):
            return arg.columns.tolist()
        elif isinstance(arg, (list, tuple)):
            return [self._label_or_arg(df, subarg) for subarg in arg]
        else:
            return arg


    def _args_eval(self, df, args):
        return [df]+[self._label_or_arg(df, arg) for arg in args]


    def _kwargs_eval(self, df, kwargs):
        return {k:self._label_or_arg(df, v) for k,v in kwargs.items()}


    def __call__(self, *args, **kwargs):
        assert (len(args) > 0) and (isinstance(args[0], pd.DataFrame))
        if len(args) > 1:
            args = self._args_eval(args[0], args[1:])
        if len(kwargs) > 0:
            kwargs = self._kwargs_eval(args[0], kwargs)
        return self.function(*args, **kwargs)



def _arg_extractor(args):
    """Extracts arguments from lists or tuples and returns them
    "flattened" (extracting lists within lists to a flat list).

    Args:
        args: can be any argument.

    Returns:
        list
    """
    flat = []
    for arg in args:
        if isinstance(arg, (list, tuple, pd.Index)):
            flat.extend(_arg_extractor(arg))
        else:
            flat.append(arg)
    return flat


def flatten_arguments(f):
    """Decorator that "flattens" any arguments contained inside of lists or
    tuples. Designed primarily for selection and dropping functions.

    Example:
        args = (a, b, (c, d, [e, f, g]))
        becomes
        args = (a, b, c, d, e, f, g)

    Args:
        f (function): function for which the arguments should be flattened.

    Returns:
        decorated function
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        assert len(args) > 0 and isinstance(args[0], pd.DataFrame)
        if len(args) > 1:
            flat_args = [args[0]]+_arg_extractor(args[1:])
            return f(*flat_args, **kwargs)
        else:
            return f(*args, **kwargs)
    return wrapped


def join_index_arguments(f):
    """Decorator for joining indexing arguments together. Designed primarily for
    `row_slice` to combine arbitrary single indices and lists of indices
    together.

    Example:
        args = (1, 2, 3, [4, 5], [6, 7])
        becomes
        args = ([1, 2, 3, 4, 5, 6, 7])

    Args:
        f (function): function to be decorated.

    Returns:
        decorated function
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        assert (len(args) > 0) and (isinstance(args[0], pd.DataFrame))
        if len(args) > 1:
            args_ = reduce(lambda x, y: np.concatenate([np.atleast_1d(x), np.atleast_1d(y)]),
                           args[1:])
            args = [args[0]] + [np.atleast_1d(args_)]
        return f(*args, **kwargs)
    return wrapped



def _col_ind_to_position(columns, indexer):
    """Converts column indexers to their integer position.

    Args:
        columns (list): list of column names.
        indexer (str or int): either a column name or an integer position of the
            column.

    Returns:
        Integer column position.
    """
    if isinstance(indexer, str):
        if indexer not in columns:
            raise Exception("String label "+str(indexer)+' is not in columns.')
        return columns.index(indexer)
    elif isinstance(indexer, int):
        if indexer < 0:
            raise Exception("Int label "+str(indexer)+' is negative. Not currently allowed.')
        return indexer
    else:
        raise Exception("Column indexer not of type str or int.")



def _col_ind_to_label(columns, indexer):
    """Converts column indexers positions to their string label.

    Args:
        columns (list): list of column names.
        indexer (int or str): either a column name or an integer position of
            the column.

    Returns:
        String column name.
    """
    if isinstance(indexer, str):
        return indexer
    elif isinstance(indexer, int):
        warnings.warn('Int labels will be inferred as column positions.')
        if indexer < 0:
            raise Exception(str(indexer)+' is negative. Not currently allowed.')
        elif indexer >= len(columns):
            raise Exception(str(indexer)+' is greater than length of columns.')
        else:
            return columns[indexer]
    else:
        raise Exception("Label not of type str or int.")


def column_indices_as_labels(f):
    """Decorator that convertes column indicies to label. Typically decoration
    occurs after decoration by `symbolic_reference`.

    Args:
        f (function): function to be decorated.

    Returns:
        Decorated function with any column indexers converted to their string
        labels.
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        assert (len(args) > 0) and (isinstance(args[0], pd.DataFrame))
        if len(args) > 1:
            label_args = [_col_ind_to_label(args[0].columns.tolist(), arg)
                          for arg in args[1:]]
            args = [args[0]]+label_args
        return f(*args, **kwargs)
    return wrapped


def column_indices_as_positions(f):
    """Decorator that converts column indices to integer position. Typically
    decoration occurs after decoration by `symbolic_reference`.

    Args:
        f (function): function to be decorated.

    Returns:
        Decorated function with any column indexers converted to their integer
        positions.
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        assert (len(args) > 0) and (isinstance(args[0], pd.DataFrame))
        if len(args) > 1:
            label_args = [_col_ind_to_position(args[0].columns.tolist(), arg)
                          for arg in args[1:]]
            args = [args[0]]+label_args
        return f(*args, **kwargs)
    return wrapped



def label_selection(f):
    """Convenience chain of decorators for functions that operate with the
    expectation of having column labels as arguments (despite user potentially
    providing symbolic `pandas.Series` objects or integer column positions).

    Args:
        f (function): function to be decorated.

    Returns:
        Decorated function with any column indexers converted to their string
        labels and arguments flattened.
    """
    return pipe(
        symbolic_reference(
            flatten_arguments(
                column_indices_as_labels(f)
            )
        )
    )


def positional_selection(f):
    """Convenience chain of decorators for functions that operate with the
    expectation of having column integer positions as arguments (despite
    user potentially providing symbolic `pandas.Series` objects or column labels).

    Args:
        f (function): function to be decorated.

    Returns:
        Decorated function with any column indexers converted to their integer
        positions and arguments flattened.
    """
    return pipe(
        symbolic_reference(
            flatten_arguments(
                column_indices_as_positions(f)
            )
        )
    )



def dfpipe(f):
    """Standard chain of decorators for a function to be used with dfply.
    The function can be chained with >> by `pipe`, application of the function
    to grouped DataFrames is enabled by `group_delegation`, and symbolic
    arguments are evaluated as-is using a default `symbolic_evaluation`.

    Args:
        f (function): function to be decorated.

    Returns:
        Decorated function chaining the `pipe`, `group_delegation`, and
        `symbolic_evaluation` decorators.
    """
    return pipe(
        group_delegation(
            symbolic_evaluation(f)
        )
    )


# ------------------------------------------------------------------------------
# Series functions
# ------------------------------------------------------------------------------

class make_symbolic(object):
    """Turns a function into a symbolic function, whose evaluation will be
    delayed until it has access to the DataFrame to be evaluated against.

    Args:
        f (function): function to be converted to symbolic.

    Returns:
        symbolic function
    """

    __name__ = "make_symbolic"

    def __init__(self, function):
        self.function = function


    def symbolic_list_handler(self, *args):
        return args


    def check_arg(self, arg):
        if isinstance(arg, (list, tuple)):
            conv_arg = [self.check_arg(subarg) for subarg in arg]
            return symbolic.sym_call(self.symbolic_list_handler, *conv_arg)
        else:
            return arg


    def __call__(self, *args, **kwargs):
        args = [self.check_arg(arg) for arg in args]
        kwargs = {k:self.check_arg(v) for k,v in kwargs.items()}
        return symbolic.sym_call(self.function, *args, **kwargs)



def order_series_by(series, order_series):
    """Orders one series according to another series, or a list of other
    series. If a list of other series are specified, ordering is done hierarchically
    like when a list of columns is supplied to `.sort_values()`.

    Args:
        series (:obj:`pandas.Series`): the pandas Series object to be reordered.
        order_series: either a pandas Series object or a list of pandas Series
            objects. These will be sorted using `.sort_values()` with
            `ascending=True`, and the new order will be used to reorder the
            Series supplied in the first argument.

    Returns:
        reordered `pandas.Series` object

    """

    if isinstance(order_series, (list, tuple)):
        sorter = pd.concat(order_series, axis=1)
        sorter_columns = ['_sorter'+str(i) for i in range(len(order_series))]
        sorter.columns = sorter_columns
        sorter['series'] = series.values
        sorted_series = sorter.sort_values(sorter_columns)['series']
        return sorted_series
    else:
        sorted_series = pd.DataFrame({
            'series':series.values,
            'order':order_series.values
        }).sort_values('order', ascending=True)['series']
        return sorted_series


def desc(series):
    """Mimics the functionality of the R desc function. Essentially inverts a
    series object to make ascending sort act like descending sort.

    Example:

    First group by cut, then find the first value of price when ordering by
    price ascending, and ordering by price descending using the `desc` function.

    diamonds >> group_by(X.cut) >> summarize(carat_low=first(X.price, order_by=X.price),
                                             carat_high=first(X.price, order_by=desc(X.price)))

             cut  carat_high  carat_low
    0       Fair       18574        337
    1       Good       18788        327
    2      Ideal       18806        326
    3    Premium       18823        326
    4  Very Good       18818        336

    Args:
        series (:obj:`pandas.Series`): pandas series to be inverted prior to
            ordering/sorting.

    Returns:
        inverted `pandas.Series`. The returned series will be numeric (integers),
            regardless of the type of the original series.

    """
    return series.rank(method='min', ascending=False)
