# Auto-import all parsers so @register_parser fires
from .ansible_parser import *
from .cargo_test_parser import *
from .cpp_parser import *
from .generic_parser import *
from .go_test_parser import *
from .junit_gradle_parser import *
from .mocha_jest_parser import *
from .pytest_parser import *
