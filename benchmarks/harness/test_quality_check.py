"""Tests for quality_check.py (P5-1 structural checks)."""
from __future__ import annotations
import pytest
from quality_check import check_impl, QualityScore


# ---------------------------------------------------------------------------
# feature_dict_pop_last
# ---------------------------------------------------------------------------

DICT_POP_LAST_GOOD = """\
+static PyObject *
+dict_pop_last_impl(PyDictObject *self)
+{
+    if (self->ma_used == 0) {
+        PyErr_SetString(PyExc_KeyError, "pop_last from an empty dict");
+        return NULL;
+    }
+    PyObject *key, *value;
+    /* ... walk entries in reverse ... */
+    PyObject *result = PyTuple_Pack(2, key, value);
+    return result;
+}
+
+PyMethodDef dict_methods[] = {
+    {"pop_last", (PyCFunction)dict_pop_last_impl, METH_NOARGS, PyDoc_STR("...")},
+    {NULL, NULL},
+};
"""

DICT_POP_LAST_MINIMAL = """\
+static PyObject *
+dict_pop_last(PyDictObject *mp)
+{
+    PyTuple_New(2);
+    {"pop_last", dict_pop_last, METH_NOARGS};
+    PyErr_SetString(PyExc_KeyError, "empty");
+    ma_used == 0;
+}
"""

def test_dict_pop_last_good_diff():
    score = check_impl("feature_dict_pop_last", "vectr", DICT_POP_LAST_GOOD)
    assert score.total > 0
    assert score.passed >= 4, score.summary()

def test_dict_pop_last_minimal_diff():
    score = check_impl("feature_dict_pop_last", "vanilla", DICT_POP_LAST_MINIMAL)
    assert score.passed >= 4, score.summary()

def test_dict_pop_last_empty_diff():
    score = check_impl("feature_dict_pop_last", "vanilla", "")
    assert score.passed == 0
    assert any(not c.passed for c in score.checks)

def test_dict_pop_last_no_pymethod():
    diff = "+static PyObject *\n+dict_pop_last_impl(PyDictObject *mp) {}\n"
    score = check_impl("feature_dict_pop_last", "vanilla", diff)
    pymethod_check = next(c for c in score.checks if "PyMethodDef" in c.name)
    assert not pymethod_check.passed


# ---------------------------------------------------------------------------
# cross_session_set_cartesian
# ---------------------------------------------------------------------------

SET_CARTESIAN_GOOD = """\
+static PyObject *
+set_cartesian_product(PySetObject *self, PyObject *other)
+{
+    PyObject *result = make_new_set(&PyFrozenSet_Type, NULL);
+    PyObject *tuple = PyTuple_New(2);
+    Py_INCREF(key_a);
+    Py_DECREF(tuple);
+    {"cartesian_product", (PyCFunction)set_cartesian_product, METH_O, "..."},
+    return result;
+}
"""

def test_set_cartesian_good():
    score = check_impl("cross_session_set_cartesian", "vectr", SET_CARTESIAN_GOOD)
    assert score.passed >= 4, score.summary()

def test_set_cartesian_missing_frozenset():
    diff = "+static PyObject *\n+cartesian_product(PySetObject *self) {}\n"
    diff += "+{\"cartesian_product\", cartesian_product, METH_O}\n"
    diff += "+PyTuple_Pack(2, a, b);\n"
    diff += "+Py_INCREF(a);\n"
    score = check_impl("cross_session_set_cartesian", "vanilla", diff)
    frozenset_check = next(c for c in score.checks if "frozenset" in c.name)
    assert not frozenset_check.passed


# ---------------------------------------------------------------------------
# cross_session_bytes_find_all
# ---------------------------------------------------------------------------

BYTES_FIND_ALL_GOOD = """\
+static PyObject *
+bytes_find_all(PyBytesObject *self, PyObject *args)
+{
+    Py_buffer sub_buf;
+    if (!PyArg_ParseTuple(args, "y*", &sub_buf)) return NULL;
+    PyObject *result = PyList_New(0);
+    Py_ssize_t pos = FASTSEARCH(buf, len, sub_buf.buf, sub_buf.len, -1, FAST_SEARCH);
+    PyList_Append(result, PyLong_FromSsize_t(pos));
+    PyBuffer_Release(&sub_buf);
+    {"find_all", bytes_find_all, METH_VARARGS, "..."},
+    return result;
+}
"""

def test_bytes_find_all_good():
    score = check_impl("cross_session_bytes_find_all", "vectr", BYTES_FIND_ALL_GOOD)
    assert score.passed >= 5, score.summary()

def test_bytes_find_all_no_buffer_release():
    diff = "+bytes_find_all()\n+Py_buffer sub;\n+PyList_New(0);\n+FASTSEARCH;\n+{\"find_all\", bytes_find_all, METH_VARARGS};\n"
    score = check_impl("cross_session_bytes_find_all", "vanilla", diff)
    release_check = next(c for c in score.checks if "PyBuffer_Release" in c.name)
    assert not release_check.passed


# ---------------------------------------------------------------------------
# cross_session_list_rotate
# ---------------------------------------------------------------------------

LIST_ROTATE_GOOD = """\
+static void
+_list_reverse_slice(PyObject **arr, Py_ssize_t lo, Py_ssize_t hi) { /* ... */ }
+
+/*[clinic input]
+list.rotate
+    n: Py_ssize_t
+/
+[clinic start generated code]*/
+static PyObject *
+list_rotate_impl(PyListObject *self, Py_ssize_t n)
+{
+    Py_ssize_t len = Py_SIZE(self);
+    if (len <= 1) Py_RETURN_NONE;
+    n %= len;
+    if (n < 0) n += len;
+    _list_reverse_slice(self->ob_item, 0, n);
+    _list_reverse_slice(self->ob_item, n, len);
+    _list_reverse_slice(self->ob_item, 0, len);
+    {"rotate", (PyCFunction)list_rotate_impl, METH_FASTCALL|METH_KEYWORDS, "..."},
+    Py_RETURN_NONE;
+}
"""

def test_list_rotate_good():
    score = check_impl("cross_session_list_rotate", "vectr", LIST_ROTATE_GOOD)
    assert score.passed >= 5, score.summary()

def test_list_rotate_missing_modulo():
    diff = "+list_rotate_impl(PyListObject *self, Py_ssize_t n) {}\n"
    diff += "+{\"rotate\", list_rotate_impl, METH_FASTCALL}\n"
    diff += "+_list_reverse_slice(self->ob_item, 0, k);\n"
    diff += "+[clinic input]\n"
    score = check_impl("cross_session_list_rotate", "vanilla", diff)
    mod_check = next(c for c in score.checks if "modulo" in c.name)
    assert not mod_check.passed


# ---------------------------------------------------------------------------
# debug_gc_finalizer
# ---------------------------------------------------------------------------

GC_FINALIZER_GOOD = """\
+import gc
+import weakref
+
+class Cycle:
+    def __init__(self): self.ref = None
+    def __del__(self): pass  # handle_legacy_finalizers in gcmodule.c
+
+a = Cycle(); b = Cycle(); a.ref = b; b.ref = a
+del a, b
+gc.collect()
+assert gc.garbage, "expected objects in gc.garbage"
+
+# Fix: use weakref.finalize instead of __del__
+def finalize_cb(): pass
+x = Cycle()
+weakref.finalize(x, finalize_cb)
"""

def test_gc_finalizer_good():
    score = check_impl("debug_gc_finalizer", "vectr", GC_FINALIZER_GOOD)
    assert score.passed >= 5, score.summary()

def test_gc_finalizer_no_weakref():
    diff = "+import gc\n+def __del__(self): pass\n+gc.collect()\n+gc.garbage\n+gcmodule\n"
    score = check_impl("debug_gc_finalizer", "vanilla", diff)
    wref_check = next(c for c in score.checks if "weakref" in c.name)
    assert not wref_check.passed


# ---------------------------------------------------------------------------
# debug_descriptor_priority
# ---------------------------------------------------------------------------

DESCRIPTOR_GOOD = """\
+class DataDesc:
+    def __get__(self, obj, typ): return 42
+    def __set__(self, obj, val): pass  # data descriptor — has both get+set
+
+class NonDataDesc:
+    def __get__(self, obj, typ): return 99  # non-data — only get
+
+class A:
+    x = DataDesc()
+class B:
+    y = NonDataDesc()
+
+a = A(); a.__dict__['x'] = 'shadow_attempt'
+assert a.x == 42, "data descriptor must not be shadowed"  # typeobject.c type_getattro
+
+b = B(); b.__dict__['y'] = 'shadowed'
+assert b.y == 'shadowed', "non-data descriptor must be shadowed by instance dict"
+# tp_descr_set check in type_getattro, Objects/typeobject.c
"""

def test_descriptor_priority_good():
    score = check_impl("debug_descriptor_priority", "vectr", DESCRIPTOR_GOOD)
    assert score.passed >= 4, score.summary()

def test_descriptor_no_citation():
    diff = "+class D:\n+    def __get__(self,o,t): return 1\n+    def __set__(self,o,v): pass\n"
    diff += "+def __get__(self,o,t): return 2\n+assert True\n"
    score = check_impl("debug_descriptor_priority", "vanilla", diff)
    cite_check = next(c for c in score.checks if "cites C" in c.name)
    assert not cite_check.passed


# ---------------------------------------------------------------------------
# Unknown task
# ---------------------------------------------------------------------------

def test_unknown_task():
    score = check_impl("unknown_task_xyz", "vanilla", "+some code")
    assert score.passed == 0
    assert score.total == 1
    assert "no checker" in score.checks[0].detail


# ---------------------------------------------------------------------------
# QualityScore properties
# ---------------------------------------------------------------------------

def test_quality_score_properties():
    score = check_impl("feature_dict_pop_last", "vectr", DICT_POP_LAST_GOOD)
    assert 0.0 <= score.score <= 1.0
    assert score.passed + (score.total - score.passed) == score.total
    summary = score.summary()
    assert "feature_dict_pop_last" in summary
    assert "vectr" in summary
