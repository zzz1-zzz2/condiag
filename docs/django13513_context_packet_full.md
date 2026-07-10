# Retry Context

## What Went Wrong

The previous attempt was too narrow and missed related code that needs the same fix. Check sibling implementations and parallel logic.

## Primary Edit Target

- **File**: `django/views/debug.py`
- **Target**: `ExceptionCycleWarning` (lines 1-50)
- **Goal**: Re-apply the key evidence the previous attempt saw but did not use in the final patch.

## Relevant Code

### Rehydrated (previously seen)

- **File**: `django/views/debug.py` (lines 1-50)
- **Relevance**: Attempt 1 viewed this span but it is absent from (or only partially present in) final PATCH_CONTEXT. Matches target keywords: ['debug', 'technical_500', 'view'].

```python
L    1 | import functools
L    2 | import re
L    3 | import sys
L    4 | import types
L    5 | import warnings
L    6 | from pathlib import Path
L    7 | 
L    8 | from django.conf import settings
L    9 | from django.http import Http404, HttpResponse, HttpResponseNotFound
L   10 | from django.template import Context, Engine, TemplateDoesNotExist
L   11 | from django.template.defaultfilters import pprint
L   12 | from django.urls import resolve
L   13 | from django.utils import timezone
L   14 | from django.utils.datastructures import MultiValueDict
L   15 | from django.utils.encoding import force_str
L   16 | from django.utils.module_loading import import_string
L   17 | from django.utils.regex_helper import _lazy_re_compile
L   18 | from django.utils.version import get_docs_version
L   19 | 
L   20 | # Minimal Django templates engine to render the error templates
L   21 | # regardless of the project's TEMPLATES setting. Templates are
L   22 | # read directly from the filesystem so that the error handler
L   23 | # works even if the template loader is broken.
L   24 | DEBUG_ENGINE = Engine(
L   25 |     debug=True,
L   26 |     libraries={'i18n': 'django.templatetags.i18n'},
L   27 | )
L   28 | 
L   29 | CURRENT_DIR = Path(__file__).parent
L   30 | 
L   31 | 
L   32 | class ExceptionCycleWarning(UserWarning):
L   33 |     pass
L   34 | 
L   35 | 
L   36 | class CallableSettingWrapper:
L   37 |     """
L   38 |     Object to wrap callable appearing in settings.
L   39 |     * Not to call in the debug page (#21345).
L   40 |     * Not to break the debug page if the callable forbidding to set attributes
# ... (truncated, 50 lines total)
```

### Rehydrated (previously seen)

- **File**: `django/views/debug.py` (lines 397-450)
- **Relevance**: Attempt 1 viewed this span but it is absent from (or only partially present in) final PATCH_CONTEXT. Matches target keywords: ['debug', 'view', '__suppress_context__'].

```python
L  397 |     def get_traceback_frames(self):
L  398 |         def explicit_or_implicit_cause(exc_value):
L  399 |             explicit = getattr(exc_value, '__cause__', None)
L  400 |             suppress_context = getattr(exc_value, '__suppress_context__', None)
L  401 |             implicit = getattr(exc_value, '__context__', None)
L  402 |             return explicit or (None if suppress_context else implicit)
L  403 | 
L  404 |         # Get the exception and all its causes
L  405 |         exceptions = []
L  406 |         exc_value = self.exc_value
L  407 |         while exc_value:
L  408 |             exceptions.append(exc_value)
L  409 |             exc_value = explicit_or_implicit_cause(exc_value)
L  410 |             if exc_value in exceptions:
L  411 |                 warnings.warn(
L  412 |                     "Cycle in the exception chain detected: exception '%s' "
L  413 |                     "encountered again." % exc_value,
L  414 |                     ExceptionCycleWarning,
L  415 |                 )
L  416 |                 # Avoid infinite loop if there's a cyclic reference (#29393).
L  417 |                 break
L  418 | 
L  419 |         frames = []
L  420 |         # No exceptions were supplied to ExceptionReporter
L  421 |         if not exceptions:
L  422 |             return frames
L  423 | 
L  424 |         # In case there's just one exception, take the traceback from self.tb
L  425 |         exc_value = exceptions.pop()
L  426 |         tb = self.tb if not exceptions else exc_value.__traceback__
L  427 | 
L  428 |         while tb is not None:
L  429 |             # Support for __traceback_hide__ which is used by a few libraries
L  430 |             # to hide internal frames.
L  431 |             if tb.tb_frame.f_locals.get('__traceback_hide__'):
L  432 |                 tb = tb.tb_next
L  433 |                 continue
L  434 |             filename = tb.tb_frame.f_code.co_filename
L  435 |             function = tb.tb_frame.f_code.co_name
L  436 |             lineno = tb.tb_lineno - 1
# ... (truncated, 54 lines total)
```

### Neighbor test: `test_exception_following_nested_client_request`

- **File**: `tests/test_client/tests.py` (lines 851-857)
- **Relevance**: Neighbor test matching concept keywords (4 matches).

```python
L  851 |     def test_exception_following_nested_client_request(self):
L  852 |         """
L  853 |         A nested test client request shouldn't clobber exception signals from
L  854 |         the outer client request.
L  855 |         """
L  856 |         with self.assertRaisesMessage(Exception, 'exception message'):
L  857 |             self.client.get('/nesting_exception_view/')
```

### Neighbor test: `test_args_kwargs_request_on_self`

- **File**: `tests/generic_views/test_base.py` (lines 232-241)
- **Relevance**: Neighbor test matching concept keywords (4 matches).

```python
L  232 |     def test_args_kwargs_request_on_self(self):
L  233 |         """
L  234 |         Test a view only has args, kwargs & request once `as_view`
L  235 |         has been called.
L  236 |         """
L  237 |         bare_view = InstanceView()
L  238 |         view = InstanceView.as_view()(self.rf.get('/'))
L  239 |         for attribute in ('args', 'kwargs', 'request'):
L  240 |             self.assertNotIn(attribute, dir(bare_view))
L  241 |             self.assertIn(attribute, dir(view))
```

## Guidelines

- Make the smallest change that fixes the issue.
- Do not modify files unrelated to the evidence above.
- Run the failing tests to verify before submitting.

## Retry Instruction

In `django/views/debug.py`, inspect `ExceptionCycleWarning` (lines 1-50). Re-apply the key evidence the previous attempt saw but did not use in the final patch. Make a minimal, local edit. When overriding factory methods that create child objects (e.g. add_subparsers), check the parent class documentation for keyword arguments that customize child instantiation — use the standard library mechanism rather than wrapping return values. Run `test_exception_following_nested_client_request, test_args_kwargs_request_on_self` to verify the fix.
