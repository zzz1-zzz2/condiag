# Retry Context

## What Went Wrong

The previous attempt looked at the right code but the patch didn't correctly apply the findings. The evidence below shows what was discovered — align the fix with it.

## Primary Edit Target

- **File**: `django/core/management/base.py`
- **Target**: `CommandParser` (lines 46-300)
- **Goal**: Re-apply the key evidence the previous attempt saw but did not use in the final patch.

## Relevant Code

### Rehydrated (previously seen)

- **File**: `django/core/management/base.py` (lines 46-300)
- **Relevance**: Attempt 1 viewed this span but it is absent from (or only partially present in) final PATCH_CONTEXT. Matches target keywords: ['base', '__init__', 'commands'].

```python
L   46 | class CommandParser(ArgumentParser):
L   47 |     """
L   48 |     Customized ArgumentParser class to improve some error messages and prevent
L   49 |     SystemExit in several occasions, as SystemExit is unacceptable when a
L   50 |     command is called programmatically.
L   51 |     """
L   52 | 
L   53 |     def __init__(
L   54 |         self, *, missing_args_message=None, called_from_command_line=None, **kwargs
L   55 |     ):
L   56 |         self.missing_args_message = missing_args_message
L   57 |         self.called_from_command_line = called_from_command_line
L   58 |         super().__init__(**kwargs)
L   59 | 
L   60 |     def parse_args(self, args=None, namespace=None):
L   61 |         # Catch missing argument for a better error message
L   62 |         if self.missing_args_message and not (
L   63 |             args or any(not arg.startswith("-") for arg in args)
L   64 |         ):
L   65 |             self.error(self.missing_args_message)
L   66 |         return super().parse_args(args, namespace)
L   67 | 
L   68 |     def error(self, message):
L   69 |         if self.called_from_command_line:
L   70 |             super().error(message)
L   71 |         else:
L   72 |             raise CommandError("Error: %s" % message)
L   73 | 
L   74 | 
L   75 | def handle_default_options(options):
L   76 |     """
L   77 |     Include any default options that all commands should accept here
L   78 |     so that ManagementUtility can handle them before searching for
L   79 |     user commands.
L   80 |     """
L   81 |     if options.settings:
L   82 |         os.environ["DJANGO_SETTINGS_MODULE"] = options.settings
L   83 |     if options.pythonpath:
L   84 |         sys.path.insert(0, options.pythonpath)
# ... (truncated, 255 lines total)
```

## Guidelines

- Make the smallest change that fixes the issue.
- Do not modify files unrelated to the evidence above.
- Run the failing tests to verify before submitting.

## Retry Instruction

In `django/core/management/base.py`, inspect `CommandParser` (lines 46-300). Re-apply the key evidence the previous attempt saw but did not use in the final patch. Make a minimal, local edit. When overriding factory methods that create child objects (e.g. add_subparsers), check the parent class documentation for keyword arguments that customize child instantiation — use the standard library mechanism rather than wrapping return values.
