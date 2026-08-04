from ohmpy.theme_manager import ThemeManager


def test_language_preference_is_saved_and_loaded(tmp_path):
    manager = ThemeManager()
    manager.user_dir = str(tmp_path)
    manager.config_path = str(tmp_path / manager.CONFIG_FILENAME)

    assert manager.load_language() == 'en'
    assert manager.save_language('es')
    assert manager.load_language() == 'es'
    assert not manager.save_language('fr')
