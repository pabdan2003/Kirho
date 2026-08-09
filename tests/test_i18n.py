from ohmpy.theme_manager import ThemeManager


def test_language_preference_is_saved_and_loaded(tmp_path):
    manager = ThemeManager()
    manager.user_dir = str(tmp_path)
    manager.config_path = str(tmp_path / manager.CONFIG_FILENAME)

    assert manager.load_language() == 'en'
    assert manager.save_language('es')
    assert manager.load_language() == 'es'
    assert not manager.save_language('fr')


def test_dark_theme_is_the_default_for_a_new_configuration(tmp_path):
    manager = ThemeManager()
    manager.user_dir = str(tmp_path)
    manager.config_path = str(tmp_path / manager.CONFIG_FILENAME)

    assert manager.load_selection() == 'dark'
    assert manager.load_theme('dark')['wire'] == '#bdbdbd'


def test_user_themes_folder_includes_a_guide_and_template(tmp_path):
    manager = ThemeManager()
    manager.user_dir = str(tmp_path)
    manager.config_path = str(tmp_path / manager.CONFIG_FILENAME)

    path = tmp_path / 'themes'
    assert manager.ensure_user_themes_dir() == str(path)
    assert (path / 'README.md').is_file()
    assert (path / 'theme-template.json.example').is_file()
