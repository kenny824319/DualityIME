"""DualityIME engine: mixed Zhuyin/English input without Shift."""
import os

from .bigram import BigramModel
from .composer import Composer, tokenize, convert
from .config import Config, DEFAULTS
from .lexicon import Lexicon, UserModel, load_english
from .zhuyin import SyllableTable


def user_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DualityIME")


def default_user_path():
    return os.path.join(user_dir(), "user.json")


def default_config_path():
    return os.path.join(user_dir(), "config.json")


def load_resources(user_path=None, config_path=None):
    """Everything a Composer needs; the big parts can be shared by many."""
    lexicon = Lexicon()
    return (SyllableTable(lexicon.entries), lexicon, load_english(),
            UserModel(user_path), BigramModel(lexicon), Config(config_path))


def create_composer(user_path=None, config_path=None):
    return Composer(*load_resources(user_path, config_path))
