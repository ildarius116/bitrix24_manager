"""Центр темизации GUI bitrix24 «Рабочий день».

Вендоренная (адаптированная) копия design system из проекта
United_Stand_Platform (`core/services/theme_service.py`). Живой зависимости от
USP нет — палитра и механизм `@token@`-подстановки перенесены сюда.

Структура файла
---------------
A. Чистые функции (без Qt) — ``PALETTES``, ``build_qss``, ``resolve_effective``,
   ``palette_for``. Тестируются headless, импортируются без QApplication.

B. Qt-часть — ``ThemeManager(QObject)`` + модульный синглтон ``theme``.
   Управляет применением темы, кэширует шаблон ``resources/styles/main.qss``,
   эмитит ``theme_changed(palette)`` при каждом ``apply()``.

Использование
-------------
::

    from gui.theme import theme

    theme.apply(app, "light")     # явно светлая
    theme.apply(app, "dark")      # явно тёмная
    theme.apply(app, "system")    # следить за темой ОС

    # подписка вьюхи:
    theme.theme_changed.connect(self.apply_palette)
    self.apply_palette(theme.current_palette())   # стартовая раскраска

Инварианты
----------
- ``PALETTES["light"]`` и ``PALETTES["dark"]`` имеют одинаковый набор ключей.
- Литеральные hex — только внутри ``PALETTES``.
- ``build_qss`` бросает исключение, если в шаблоне остались незаменённые токены
  ``@...@`` или нужный ключ отсутствует в палитре.

Отличия от USP
--------------
- Пути к ресурсам упрощены: PyInstaller/frozen-режим не поддерживается
  (приложение запускается из исходников). Шаблон ищется относительно корня
  проекта: ``<project_root>/resources/styles/main.qss``.
"""

from __future__ import annotations

import re

# ============================================================
# A. ЧИСТЫЕ ФУНКЦИИ — без импорта PySide6
# ============================================================

# ---------------------------------------------------------------------------
# Палитра токенов (light/dark пары перенесены из USP; ключевые токены bitrix24
# зафиксированы в GUI_SPEC §3.4/§8: accent #0078d4, status_*, text_muted,
# log_bg #1e1e1e и log_text #d4d4d4 — лог всегда тёмный в обеих темах).
# ---------------------------------------------------------------------------

PALETTES: dict[str, dict[str, str]] = {
    "light": {
        # --- Акцент (Fluent Windows Blue) ---
        "accent":           "#0078d4",   # акцент: баннер, CTA, активные пилюли
        "accent_hover":     "#106ebe",   # hover accent-кнопок
        "accent_pressed":   "#1a4a73",   # нажатие accent-кнопок
        "accent_deep":      "#005a9e",   # тёмный акцент
        "accent_soft":      "#e8f1fb",   # мягкий акцент-фон (наведение/чек)

        # --- Фоны ---
        "bg_window":        "#f3f3f3",   # фон главного окна (QMainWindow)
        "bg_surface":       "#ffffff",   # панели, таблицы, инпуты
        "bg_surface_alt":   "#f9f9f9",   # чередование строк таблицы (=surface_raised)
        "surface_raised":   "#f9f9f9",   # приподнятые блоки (секция РЕЖИМ)
        "ribbon_bg":        "#f5f6f8",   # фон полосы режима/контролов

        # --- Рамки ---
        "border":           "#d1d1d1",   # рамки/разделители
        "border_light":     "#e5e5e5",   # более светлые рамки

        # --- Поверхности/наведение ---
        "menu_hover":       "#eef3f8",   # наведение в меню-баре / gridline
        "hover":            "#e8f1fb",   # наведение (пункты меню, кнопки)
        "hover_checked":    "#d4e7f9",   # hover для checked toggle
        "table_header_bg":  "#f0f0f0",   # фон заголовка таблицы

        # --- Текст ---
        "text":             "#1a1a1a",   # основной текст
        "text_secondary":   "#424242",   # меню-текст, заголовки
        "text_medium":      "#555555",   # метки секций, статус-текст
        "text_muted":       "#888888",   # приглушённый / skipped
        "text_faint":       "#6a6a6a",   # очень приглушённый
        "text_primary_alt": "#424242",   # текст кнопок (=text_secondary)

        # --- Инпуты/скроллбар ---
        "input_disabled":   "#a0a0a0",   # disabled input цвет
        "btn_disabled_bg":  "#e5e5e5",   # фон disabled-кнопки (=border_light)
        "scrollbar":        "#c1c1c1",   # handle скроллбара
        "scrollbar_hover":  "#a8a8a8",   # handle скроллбара при hover

        # --- Прогресс/бренд ---
        "brand_cyan":       "#29abe2",   # бренд-циан (акцент прогресса)
        "progress_end":     "#29abe2",   # конец градиента прогресс-бара

        # --- Консоль/лог (всегда тёмная — одинаково в обеих темах) ---
        "log_bg":           "#1e1e1e",   # фон консоли/лога
        "log_text":         "#d4d4d4",   # текст лога
        "log_selection":    "#264f78",   # selection-bg (VS Code Dark+)

        # --- Статусы (индикация в таблице/консоли/индикаторе) ---
        "status_ok":        "#107c10",   # filled / repaired / вебхук подключён
        "status_fail":      "#d13438",   # error / вебхук недоступен
        "status_running":   "#0078d4",   # dry-run / выполняется
        "status_warning":   "#d9a400",   # aborted / предупреждение
        "status_pending":   "#555555",   # ожидание
        "status_na":        "#7a8089",   # неизвестно/неприменимо
    },
    "dark": {
        # --- Акцент ---
        "accent":           "#3aa0ff",
        "accent_hover":     "#5cb0ff",
        "accent_pressed":   "#cce4f6",
        "accent_deep":      "#8fcbff",
        "accent_soft":      "#243748",

        # --- Фоны ---
        "bg_window":        "#1f1f1f",
        "bg_surface":       "#2b2b2b",
        "bg_surface_alt":   "#333333",
        "surface_raised":   "#333333",
        "ribbon_bg":        "#262626",

        # --- Рамки ---
        "border":           "#3d3d3d",
        "border_light":     "#333333",

        # --- Поверхности/наведение ---
        "menu_hover":       "#333a42",
        "hover":            "#2d3a47",
        "hover_checked":    "#34465a",
        "table_header_bg":  "#2f2f2f",

        # --- Текст ---
        "text":             "#e8e8e8",
        "text_secondary":   "#c8c8c8",
        "text_medium":      "#b0b0b0",
        "text_muted":       "#888888",
        "text_faint":       "#aaaaaa",
        "text_primary_alt": "#c8c8c8",

        # --- Инпуты/скроллбар ---
        "input_disabled":   "#666666",
        "btn_disabled_bg":  "#333333",
        "scrollbar":        "#4a4a4a",
        "scrollbar_hover":  "#5e5e5e",

        # --- Прогресс/бренд ---
        "brand_cyan":       "#29abe2",
        "progress_end":     "#29abe2",

        # --- Консоль/лог (всегда тёмная) ---
        "log_bg":           "#1e1e1e",
        "log_text":         "#d4d4d4",
        "log_selection":    "#264f78",

        # --- Статусы ---
        "status_ok":        "#4ec94e",
        "status_fail":      "#e85d6a",
        "status_running":   "#3aa0ff",
        "status_warning":   "#e8c000",
        "status_pending":   "#c8c8c8",
        "status_na":        "#9aa0a6",
    },
}

# Регулярное выражение для поиска токенов вида @token_name@
_TOKEN_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)@")


def build_qss(template_text: str, palette: dict[str, str]) -> str:
    """Подставить токены ``@token@`` из палитры в шаблон QSS.

    Parameters
    ----------
    template_text:
        Текст шаблона QSS с плейсхолдерами вида ``@token_name@``.
    palette:
        Словарь ``{token_name: hex_color}``.

    Returns
    -------
    str
        Готовый QSS с подставленными hex-значениями.

    Raises
    ------
    KeyError
        Если токен из шаблона отсутствует в ``palette``.
    ValueError
        Если после подстановки остались незаменённые ``@...@`` (защита от
        рассинхрона шаблона и палитры).
    """
    errors: list[str] = []

    def _replace(match: "re.Match[str]") -> str:
        token = match.group(1)
        if token not in palette:
            errors.append(token)
            return match.group(0)  # оставляем нетронутым, чтобы собрать все ошибки
        return palette[token]

    result = _TOKEN_RE.sub(_replace, template_text)

    if errors:
        raise KeyError(f"build_qss: токены отсутствуют в палитре: {errors!r}")

    remaining = re.findall(r"@[^@\s]+@", result)
    if remaining:
        raise ValueError(
            f"build_qss: незаменённые токены остались в шаблоне: {remaining!r}"
        )

    return result


def _detect_system_theme_winreg() -> str:
    """Определить тему Windows через реестр (резервный путь).

    Читает ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\
    Personalize\\AppsUseLightTheme``: ``0`` = тёмная, ``1`` = светлая.

    Returns
    -------
    str
        ``"light"`` или ``"dark"``. При любой ошибке — ``"light"``.
    """
    try:
        import winreg  # доступен только на Windows

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(value) == 1 else "dark"
    except Exception:
        return "light"


def _resolve_system_theme() -> str:
    """Определить текущую тему ОС.

    Сначала пробует Qt ``colorScheme`` (основной путь), затем winreg (fallback).
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
            if scheme == Qt.ColorScheme.Light:
                return "light"
            # Qt.ColorScheme.Unknown → fallback на winreg
    except Exception:
        pass

    return _detect_system_theme_winreg()


def resolve_effective(mode: str) -> str:
    """Преобразовать режим темы в конкретный ``"light"`` или ``"dark"``.

    Parameters
    ----------
    mode:
        ``"light"``, ``"dark"`` или ``"system"``. Неизвестное значение → ``"light"``.

    Returns
    -------
    str
        ``"light"`` или ``"dark"``.
    """
    if mode == "light":
        return "light"
    if mode == "dark":
        return "dark"
    if mode == "system":
        return _resolve_system_theme()
    return "light"


def palette_for(mode: str) -> dict[str, str]:
    """Вернуть копию палитры для заданного режима темы."""
    return dict(PALETTES[resolve_effective(mode)])


# ============================================================
# B. Qt-ЧАСТЬ — ThemeManager + синглтон theme
# ============================================================

from pathlib import Path  # noqa: E402

from PySide6.QtCore import QObject, Signal  # noqa: E402  (после чистых функций)

# Корень проекта = родитель каталога gui/. Шаблон стилей лежит рядом с src/.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STYLE_PATH = _PROJECT_ROOT / "resources" / "styles" / "main.qss"


class ThemeManager(QObject):
    """Менеджер тем — синглтон для применения и отслеживания темы интерфейса.

    Импортируется окном и виджетами через модульный синглтон ``theme``::

        from gui.theme import theme

    Сигналы
    -------
    theme_changed : Signal(dict)
        Эмитируется после каждого ``apply()`` с активной палитрой ``dict[str,str]``.

    Потокобезопасность
    ------------------
    ``apply()`` вызывается из GUI-потока (как ``app.setStyleSheet``).
    """

    theme_changed = Signal(dict)

    def __init__(self, parent: "QObject | None" = None) -> None:
        super().__init__(parent)
        self._mode: str = "light"
        self._palette: dict[str, str] = dict(PALETTES["light"])
        self._template_cache: "str | None" = None
        self._system_connection_active: bool = False

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def current_palette(self) -> dict[str, str]:
        """Вернуть копию текущей активной палитры ``{token: hex}``."""
        return dict(self._palette)

    def current_mode(self) -> str:
        """Вернуть текущий выбранный режим (``light``/``dark``/``system``).

        Это выбранный пользователем режим, а не резолвенный эффективный.
        """
        return self._mode

    def apply(self, app, mode: str) -> None:
        """Применить тему к приложению.

        Резолвит ``mode`` → эффективный ``"light"``/``"dark"``, формирует QSS из
        шаблона и палитры, устанавливает стиль приложения, эмитит
        ``theme_changed(palette)``. Шаблон ``resources/styles/main.qss`` читается
        один раз и кэшируется.

        Parameters
        ----------
        app:
            Экземпляр ``QApplication``.
        mode:
            ``"light"``, ``"dark"`` или ``"system"``.
        """
        prev_mode = self._mode
        self._mode = mode
        self._update_system_subscription(app, prev_mode, mode)

        effective = resolve_effective(mode)
        palette = dict(PALETTES[effective])

        template = self._load_template()
        qss = build_qss(template, palette)

        app.setStyleSheet(qss)

        self._palette = palette
        self.theme_changed.emit(palette)

    # ------------------------------------------------------------------
    # Внутренняя реализация
    # ------------------------------------------------------------------

    def _load_template(self) -> str:
        """Загрузить шаблон main.qss (однократно, с кэшированием)."""
        if self._template_cache is not None:
            return self._template_cache

        if _STYLE_PATH.exists():
            self._template_cache = _STYLE_PATH.read_text(encoding="utf-8")
        else:
            self._template_cache = ""

        return self._template_cache

    def _update_system_subscription(self, app, prev_mode: str, new_mode: str) -> None:
        """Управлять подпиской на ``colorSchemeChanged``.

        Подписываемся только при переходе в ``"system"``; отписываемся при выходе.
        """
        if new_mode == "system" and not self._system_connection_active:
            try:
                app.styleHints().colorSchemeChanged.connect(
                    lambda: self._on_system_scheme_changed(app)
                )
                self._system_connection_active = True
            except Exception:
                # Сигнал недоступен (старый Qt / не-Windows) — просто не следим.
                pass
        elif new_mode != "system" and self._system_connection_active:
            try:
                app.styleHints().colorSchemeChanged.disconnect()
            except Exception:
                pass
            self._system_connection_active = False

    def _on_system_scheme_changed(self, app) -> None:
        """Повторно применить тему при смене light/dark на уровне ОС."""
        try:
            self.apply(app, "system")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Модульный синглтон — единая точка доступа.
# ---------------------------------------------------------------------------
theme = ThemeManager()
