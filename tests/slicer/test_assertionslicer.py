#  This file is part of Pynguin.
#
#  SPDX-FileCopyrightText: 2019–2022 Pynguin Contributors
#
#  SPDX-License-Identifier: LGPL-3.0-or-later
#
import importlib
import inspect
import threading

import pytest

import pynguin.assertion.assertion as ass
import pynguin.configuration as config
import pynguin.testcase.defaulttestcase as dtc
import pynguin.testcase.statement as stmt
import pynguin.utils.generic.genericaccessibleobject as gao
from pynguin.analyses.types import InferredSignature
from pynguin.instrumentation.instrumentation import (
    CheckedCoverageInstrumentation,
    InstrumentationTransformer,
)
from pynguin.instrumentation.machinery import install_import_hook
from pynguin.slicer.dynamicslicer import AssertionSlicer
from pynguin.testcase.execution import ExecutionTracer, TestCaseExecutor
from pynguin.testcase.testcase import TestCase
from tests.fixtures.linecoverage.plus import Plus


class ListTest:
    attribute = [1, 2, 3]


def _get_default_plus_test():
    test_case = dtc.DefaultTestCase()

    # int_0 = 42
    int_stmt = stmt.IntPrimitiveStatement(test_case, 42)

    # plus_0 = module_0.Plus()
    constructor_call = stmt.ConstructorStatement(
        test_case,
        gao.GenericConstructor(
            Plus,
            InferredSignature(
                signature=inspect.signature(Plus.__init__),
                parameters={},
                return_type=Plus,
            ),
        ),
    )

    # int_1 = plus0.plus_four(var_0)
    method_call = stmt.MethodStatement(
        test_case,
        gao.GenericMethod(
            Plus,
            Plus.plus_four,
            InferredSignature(
                signature=inspect.signature(Plus.plus_four),
                parameters={"number": int},
                return_type=int,
            ),
        ),
        constructor_call.ret_val,
        {"number": int_stmt.ret_val},
    )

    test_case.add_statement(int_stmt)
    test_case.add_statement(constructor_call)
    test_case.add_statement(method_call)
    return test_case


def _get_default_list_test():
    test_case = dtc.DefaultTestCase()

    # listtest_0 = module_0.ListTest()
    constructor_call = stmt.ConstructorStatement(
        test_case,
        gao.GenericConstructor(
            ListTest,
            InferredSignature(
                signature=inspect.signature(ListTest.__init__),
                parameters={},
                return_type=ListTest,
            ),
        ),
    )

    # attribute_0 = listtest_0.attribute
    list_attribute_call = stmt.FieldStatement(
        test_case,
        gao.GenericField(owner=ListTest, field="attribute", field_type=list),
        constructor_call.ret_val,
    )

    test_case.add_statement(constructor_call)
    test_case.add_statement(list_attribute_call)
    return test_case


def get_plus_test_with_object_assertion() -> TestCase:
    """
    Generated testcase:
        int_0 = 42
        plus_0 = module_0.Plus()
        int_1 = plus_0.plus_four(var_0)
        assert int_1 == 46
    """
    test_case = _get_default_plus_test()
    test_case.statements[-1].add_assertion(
        ass.ObjectAssertion(test_case.statements[-1].ret_val, 46)
    )
    return test_case


def _get_plus_test_with_float_assertion() -> TestCase:
    """
    Generated testcase:
        int_0 = 42
        plus_0 = module_0.Plus()
        int_1 = plus_0.plus_four(int_0)
        assert int_1 == pytest.approx(46, rel=0.01, abs=0.01)
    """
    test_case = _get_default_plus_test()
    test_case.statements[-1].add_assertion(
        ass.FloatAssertion(test_case.statements[-1].ret_val, 46)
    )
    return test_case


def _get_plus_test_with_not_none_assertion() -> TestCase:
    """
    Generated testcase:
        int_0 = 42
        plus_0 = module_0.Plus()
        int_1 = plus_0.plus_four(int_0)
        assert int_1 is not None
    """
    test_case = _get_default_plus_test()
    test_case.statements[-1].add_assertion(
        ass.NotNoneAssertion(test_case.statements[-1].ret_val)
    )
    return test_case


def _get_list_test_with_len_assertion() -> TestCase:
    """
    Generated testcase:
        list_test_0 = module_0.ListTest()
        list_0 = list_test_0.attribute
        assert len(list_0) == 3
    """
    test_case = _get_default_list_test()
    test_case.statements[-1].add_assertion(
        ass.CollectionLengthAssertion(test_case.statements[-1].ret_val, 3)
    )
    return test_case


@pytest.mark.parametrize(
    "module_name, test_case, expected_lines",
    [
        (
            "tests.fixtures.linecoverage.plus",
            get_plus_test_with_object_assertion(),
            {9, 16, 18},
        ),
        (
            "tests.fixtures.linecoverage.plus",
            _get_plus_test_with_float_assertion(),
            {9, 16, 18},
        ),
    ],
)
def test_slicing_after_test_execution(module_name, test_case, expected_lines):
    config.configuration.statistics_output.coverage_metrics = [
        config.CoverageMetric.CHECKED
    ]

    tracer = ExecutionTracer()
    tracer.current_thread_identifier = threading.current_thread().ident

    with install_import_hook(module_name, tracer):
        module = importlib.import_module(module_name)
        importlib.reload(module)

        executor = TestCaseExecutor(tracer)
        executor.execute(test_case, instrument_test=True)

        trace = tracer.get_trace()
        assertions = trace.assertion_trace.assertions
        assert assertions

        assertion_slicer = AssertionSlicer(
            trace, tracer.get_known_data().existing_code_objects
        )
        instructions_in_slice = []
        for assertion in assertions:
            instructions_in_slice.extend(assertion_slicer.slice_assertion(assertion))
        assert instructions_in_slice

        checked_lines = assertion_slicer.map_instructions_to_lines(
            instructions_in_slice
        )
        assert checked_lines
        assert checked_lines == expected_lines


@pytest.mark.parametrize(
    "module_name, expected_assertions, expected_lines",
    [
        ("tests.fixtures.assertion.basic", 1, [16]),
        ("tests.fixtures.assertion.multiple", 3, [16, 17, 18]),
        ("tests.fixtures.assertion.loop", 5, [13, 13, 13, 13, 13]),
    ],
)
def test_assertion_detection_on_module(
    module_name, expected_assertions, expected_lines
):
    module = importlib.import_module(module_name)
    module = importlib.reload(module)

    # Setup
    tracer = ExecutionTracer()
    adapter = CheckedCoverageInstrumentation(tracer)
    transformer = InstrumentationTransformer(tracer, [adapter])

    # Instrument and call module
    module.test_foo.__code__ = transformer.instrument_module(module.test_foo.__code__)
    tracer.current_thread_identifier = threading.current_thread().ident
    module.test_foo()
    assertion_trace = tracer.get_trace().assertion_trace

    assert len(assertion_trace.assertions) == expected_assertions
    for index in range(expected_assertions):
        assert (
            assertion_trace.assertions[index].traced_assertion_pop_jump.lineno
            == expected_lines[index]
        )


@pytest.mark.parametrize(
    "test_case, expected_assertions",
    [
        (get_plus_test_with_object_assertion(), 1),
        (_get_plus_test_with_float_assertion(), 1),
        (_get_plus_test_with_not_none_assertion(), 1),
        (_get_list_test_with_len_assertion(), 1),
    ],
)
def test_assertion_detection_on_test_case(test_case, expected_assertions):
    config.configuration.statistics_output.coverage_metrics = [
        config.CoverageMetric.CHECKED
    ]
    tracer = ExecutionTracer()
    tracer.current_thread_identifier = threading.current_thread().ident

    executor = TestCaseExecutor(tracer)

    executor.execute(test_case, instrument_test=True)
    assertion_trace = tracer.get_trace().assertion_trace
    assert len(assertion_trace.assertions) == expected_assertions
