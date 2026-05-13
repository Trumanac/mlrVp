"""Shared utilities for building organized, screen-safe QMenus throughout mlrVp.

Policy
------
* ≤ MAX_FLAT items   → plain flat menu, no submenus.
* > MAX_FLAT items   → items split into alphabetical submenu groups, each at
                       most COLUMN_SIZE items.  Group labels show the letter
                       range of the items they contain ("A – F", "G – M", …).
* Menu height capped to 85% of the primary screen so the menu never overflows.
* Every menu uses the shared dark stylesheet (``MENU_QSS``) for consistency.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from PySide6.QtWidgets import QMenu, QWidget, QApplication
from PySide6.QtGui import QAction


# ── Shared dark stylesheet ────────────────────────────────────────────────
MENU_QSS = (
    "QMenu {"
    "  background: #2a2a2a;"
    "  color: #cccccc;"
    "  border: 1px solid #555555;"
    "}"
    "QMenu::item {"
    "  padding: 4px 20px 4px 12px;"
    "}"
    "QMenu::item:selected {"
    "  background: #444444;"
    "}"
    "QMenu::item:disabled {"
    "  color: #555555;"
    "}"
    "QMenu::separator {"
    "  height: 1px;"
    "  background: #444444;"
    "  margin: 3px 6px;"
    "}"
)

# Items at or below this count are shown as a flat list.
MAX_FLAT: int = 12
# Target maximum items per alphabetical submenu group.
COLUMN_SIZE: int = 10


# ── Helpers ───────────────────────────────────────────────────────────────

def make_menu(parent: QWidget | None = None) -> QMenu:
    """Return a new QMenu with the standard dark stylesheet applied."""
    menu = QMenu(parent)
    menu.setStyleSheet(MENU_QSS)
    _cap_to_screen(menu)
    return menu


def _cap_to_screen(menu: QMenu) -> None:
    """Set a max height so the menu never clips off the screen."""
    screen = QApplication.primaryScreen()
    if screen:
        avail_h = screen.availableGeometry().height()
        menu.setMaximumHeight(int(avail_h * 0.85))


def _make_submenu(label: str, parent: QMenu) -> QMenu:
    sub = QMenu(label, parent)
    sub.setStyleSheet(MENU_QSS)
    _cap_to_screen(sub)
    parent.addMenu(sub)
    return sub


def _group_by_letter_range(
    items: list[tuple[str, Any]],
    column_size: int,
) -> list[tuple[str, list[tuple[str, Any]]]]:
    """Split a sorted list of (label, data) into letter-range groups.

    Returns ``[(group_label, [(label, data), …]), …]``.
    """
    groups: list[tuple[str, list[tuple[str, Any]]]] = []
    for i in range(0, len(items), column_size):
        chunk = items[i : i + column_size]
        first = chunk[0][0][0].upper()
        last = chunk[-1][0][0].upper()
        group_label = first if first == last else f"{first} – {last}"
        groups.append((group_label, chunk))
    return groups


def _add_items_to_menu(
    menu: QMenu,
    items: Iterable[tuple[str, Any]],
    on_select: Callable[[Any], None] | None,
) -> list[QAction]:
    """Add (label, data) pairs to *menu*, wiring on_select if provided."""
    actions: list[QAction] = []
    for label, data in items:
        act = menu.addAction(label)
        act.setData(data)
        if on_select is not None:
            act.triggered.connect(lambda _checked=False, _d=data: on_select(_d))
        actions.append(act)
    return actions


# ── Public API ────────────────────────────────────────────────────────────

def populate_menu(
    menu: QMenu,
    items: list[tuple[str, Any]],
    on_select: Callable[[Any], None] | None = None,
    max_flat: int = MAX_FLAT,
    column_size: int = COLUMN_SIZE,
) -> None:
    """Populate *menu* in-place with *items*.

    Parameters
    ----------
    menu:       Target QMenu (already created).
    items:      ``[(display_label, payload), …]`` — need not be pre-sorted.
    on_select:  Called with the payload when an item is activated.
    max_flat:   Items at or below this count appear as a flat list.
    column_size: Max items per alphabetical submenu group when grouped.
    """
    if not items:
        menu.addAction("(empty)").setEnabled(False)
        return

    sorted_items = sorted(items, key=lambda t: t[0].lower())

    if len(sorted_items) <= max_flat:
        _add_items_to_menu(menu, sorted_items, on_select)
    else:
        groups = _group_by_letter_range(sorted_items, column_size)
        for group_label, group_items in groups:
            if len(group_items) == 1:
                # Single item: add directly rather than a one-entry submenu.
                _add_items_to_menu(menu, group_items, on_select)
            else:
                sub = _make_submenu(group_label, menu)
                _add_items_to_menu(sub, group_items, on_select)


def build_menu(
    parent: QWidget | None,
    items: list[tuple[str, Any]],
    on_select: Callable[[Any], None] | None = None,
    max_flat: int = MAX_FLAT,
    column_size: int = COLUMN_SIZE,
) -> QMenu:
    """Create, populate, and return a styled QMenu.

    This is the primary convenience function for most long-list use-cases.
    """
    menu = make_menu(parent)
    populate_menu(menu, items, on_select, max_flat=max_flat,
                  column_size=column_size)
    return menu


def build_categorized_menu(
    parent: QWidget | None,
    categories: dict[str, list[tuple[str, Any]]],
    on_select: Callable[[Any], None] | None = None,
    max_flat_per_category: int = MAX_FLAT,
    column_size: int = COLUMN_SIZE,
) -> QMenu:
    """Create a menu with named submenu categories.

    Parameters
    ----------
    categories: ``{category_label: [(display_label, payload), …], …}``
                Insertion order is preserved.  Within each category, items
                are sorted alphabetically.
    """
    menu = make_menu(parent)
    for cat_label, cat_items in categories.items():
        if not cat_items:
            continue
        sub = _make_submenu(cat_label, menu)
        populate_menu(sub, cat_items, on_select,
                      max_flat=max_flat_per_category,
                      column_size=column_size)
    return menu
