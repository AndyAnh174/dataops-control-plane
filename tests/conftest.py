from dataops_control_plane.config import Settings

_orig_init = Settings.__init__


def _test_settings_init(self, *args, **kwargs):
    kwargs.setdefault("_env_file", None)
    _orig_init(self, *args, **kwargs)


Settings.__init__ = _test_settings_init
