"""Scrollable rack container with drag-drop reordering and add/remove."""

from PySide6.QtWidgets import (
    QScrollArea, QWidget, QVBoxLayout, QPushButton, QFrame
)
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QPen
from typing import Callable, Optional

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.scale import bind_fixed_height


# Registry populated by rack_modules package
_MODULE_REGISTRY: dict[str, type] = {}
# Category registry: preserves insertion order; category → [display_name, …]
_CATEGORY_REGISTRY: dict[str, list[str]] = {}

# Performance budget: 512 samples @ 44100 Hz = 11.6ms callback.
# ~8.9ms available after channel/master/jitter headroom.
# Heaviest effect (MoogLadder) ≈ 0.8ms; safe flat cap keeps worst-case under budget.
MAX_MODULES_PER_RACK = 8


def register_rack_module(name: str, cls: type):
    """Register an effect module class for the Add menu."""
    _MODULE_REGISTRY[name] = cls


def register_rack_module_in_category(category: str, name: str, cls: type):
    """Register a module and assign it to a named category for the Add menu."""
    _MODULE_REGISTRY[name] = cls
    if category not in _CATEGORY_REGISTRY:
        _CATEGORY_REGISTRY[category] = []
    if name not in _CATEGORY_REGISTRY[category]:
        _CATEGORY_REGISTRY[category].append(name)


def get_category_registry() -> dict[str, list[str]]:
    return {k: list(v) for k, v in _CATEGORY_REGISTRY.items()}


def get_module_registry() -> dict[str, type]:
    return dict(_MODULE_REGISTRY)


def get_module_class_by_effect_type(effect_type: str) -> Optional[type]:
    """Look up a module class by its class-level ``effect_type`` attribute.

    Returns None if no registered module declares that effect_type.
    For ``stowaway_*`` (per-instance effect_type), returns the
    ForeignExchangeModule class — caller is responsible for passing
    the slot index.
    """
    if not effect_type:
        return None
    if effect_type.startswith("stowaway_") or effect_type == "stowaway":
        from mlrvp.gui.rack_modules.stowaway import ForeignExchangeModule
        return ForeignExchangeModule
    for cls in _MODULE_REGISTRY.values():
        if getattr(cls, "effect_type", None) == effect_type:
            return cls
    return None


class RackContainer(QScrollArea):
    """A scrollable 19-inch rack case holding stacked effect modules."""

    rack_changed = Signal()           # emitted on add / remove / reorder
    module_param_changed = Signal(str, str, object)  # module_type, param, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Custom scrollbar QSS forces Qt out of native-scrollbar mode so the
        # scrollbar renders in its own reserved space (non-overlay) — no
        # viewport margin needed and the right rack ear is never covered.
        self.setStyleSheet("""
            QScrollBar:vertical {
                background: #1a1a1a;
                width: 8px;
                margin: 0;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #3a3a3a;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #555555;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
                background: none;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }
        """)

        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

        self._modules: list[RackModuleBase] = []

        # Optional callback returning True if a global (cross-rack) limit is hit
        self._global_limit_fn: Optional[Callable[[], bool]] = None

        # Add-effect placeholder
        self._add_btn = QPushButton("＋  ADD EFFECT")
        bind_fixed_height(self._add_btn, 32)
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setStyleSheet(
            "QPushButton {"
            "  background: #1a1a1a; color: #666666; border: 1px dashed #444;"
            "  font-size: 11px; font-weight: bold;"
            "}"
            "QPushButton:hover { color: #aaaaaa; border-color: #777; }"
        )
        self._add_btn.clicked.connect(self._show_add_menu)
        self._layout.addWidget(self._add_btn)
        self._layout.addStretch()

        self.setWidget(self._inner)

        # Drag state
        self._drag_index: int = -1
        self._drag_start: QPoint | None = None
        self._drop_indicator_idx: int = -1

        # Enable drop on inner widget
        self._inner.setAcceptDrops(True)
        self._inner.dragEnterEvent = self._inner_dragEnterEvent
        self._inner.dragMoveEvent = self._inner_dragMoveEvent
        self._inner.dragLeaveEvent = self._inner_dragLeaveEvent
        self._inner.dropEvent = self._inner_dropEvent
        self._inner_orig_paint = self._inner.paintEvent
        self._inner.paintEvent = self._inner_paintEvent

    # --- public API -----------------------------------------------

    def set_global_limit_fn(self, fn):
        """Set a callable that returns True if no more effects can be added globally."""
        self._global_limit_fn = fn

    def add_module(self, module: RackModuleBase, index: int = -1) -> bool:
        """Insert a module into the rack.  Returns False if the rack is full."""
        if len(self._modules) >= MAX_MODULES_PER_RACK:
            return False
        if self._global_limit_fn and self._global_limit_fn():
            return False
        module.remove_requested.connect(lambda m=module: self.remove_module(m))
        module.param_changed.connect(
            lambda p, v, m=module: self._on_module_param(m, p, v)
        )
        if index < 0 or index >= len(self._modules):
            idx = len(self._modules)
        else:
            idx = index
        self._modules.insert(idx, module)
        # Insert before add-btn (which is at position len(modules)-1 + 0 before insert)
        self._layout.insertWidget(idx, module)
        self.rack_changed.emit()
        # Auto-sync current power state so the engine enables the effect immediately.
        # Default _power_on is True, but set_power() skips emit when state unchanged.
        # Force a power cycle to guarantee the emit fires for the new module.
        if module.enabled:
            module._power_on = False
            module.set_power(True, emit=True)
        return True

    def remove_module(self, module: RackModuleBase):
        """Remove a module from the rack."""
        if module in self._modules:
            self._modules.remove(module)
            self._layout.removeWidget(module)
            module.setParent(None)
            module.deleteLater()
            self.rack_changed.emit()

    def move_module(self, from_idx: int, to_idx: int):
        """Move a module from one position to another."""
        if from_idx == to_idx:
            return
        if 0 <= from_idx < len(self._modules) and 0 <= to_idx < len(self._modules):
            mod = self._modules.pop(from_idx)
            self._modules.insert(to_idx, mod)
            # Rebuild layout order
            self._rebuild_layout()
            self.rack_changed.emit()

    def get_modules(self) -> list[RackModuleBase]:
        return list(self._modules)

    def clear(self):
        for m in list(self._modules):
            self.remove_module(m)

    # --- internal -------------------------------------------------

    def _rebuild_layout(self):
        """Re-insert all widgets in current order."""
        for i in reversed(range(self._layout.count())):
            self._layout.takeAt(i)
        for mod in self._modules:
            self._layout.addWidget(mod)
        self._layout.addWidget(self._add_btn)
        self._layout.addStretch()

    def _show_add_menu(self):
        from mlrvp.gui.widgets.overflow_menu import (
            make_menu, _make_submenu,
        )
        at_local_limit = len(self._modules) >= MAX_MODULES_PER_RACK
        at_global_limit = self._global_limit_fn and self._global_limit_fn()

        menu = make_menu(self)
        if at_local_limit or at_global_limit:
            reason = "rack full (max 8)" if at_local_limit else "total FX limit reached"
            menu.addAction(f"({reason})").setEnabled(False)
        elif _CATEGORY_REGISTRY:
            # Build one submenu per registered category.
            for cat_label, names in _CATEGORY_REGISTRY.items():
                sub = _make_submenu(cat_label, menu)
                for name in names:
                    cls = _MODULE_REGISTRY.get(name)
                    if cls is None:
                        continue
                    act = sub.addAction(name)
                    act.triggered.connect(
                        lambda checked=False, c=cls: self._add_from_menu(c)
                    )
        elif _MODULE_REGISTRY:
            # Fallback: flat alphabetical list when no categories are registered.
            for name, cls in sorted(_MODULE_REGISTRY.items()):
                act = menu.addAction(name)
                act.triggered.connect(
                    lambda checked=False, c=cls: self._add_from_menu(c)
                )
        else:
            menu.addAction("(no modules available)").setEnabled(False)
        menu.exec(self._add_btn.mapToGlobal(self._add_btn.rect().center()))

    def _add_from_menu(self, cls: type):
        # Foreign Exchange modules need a unique stowaway slot index (0–3)
        from mlrvp.gui.rack_modules.stowaway import ForeignExchangeModule
        if cls is ForeignExchangeModule:
            slot = self._get_next_stowaway_slot()
            if slot is None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "Slot Limit",
                    "Maximum of 4 Foreign Exchange modules per rack.",
                )
                return
            module = cls(slot_index=slot)
        else:
            module = cls()
        self.add_module(module)

    def _on_module_param(self, module: RackModuleBase, param: str, value):
        """Relay module param changes with module type info."""
        type_name = type(module).__name__
        self.module_param_changed.emit(type_name, param, value)

    def _get_next_stowaway_slot(self) -> int | None:
        """Return lowest unused stowaway index (0–3), or None if all taken."""
        used = set()
        for mod in self._modules:
            et = getattr(mod, "effect_type", "")
            if et.startswith("stowaway_"):
                try:
                    used.add(int(et.split("_", 1)[1]))
                except (ValueError, IndexError):
                    pass
        for i in range(4):
            if i not in used:
                return i
        return None

    # --- drag-drop reorder ----------------------------------------

    def _module_for_mime(self, mime_data) -> RackModuleBase | None:
        """Find the module referenced by the drag MIME data."""
        data = mime_data.data("application/x-rack-module-id")
        if not data:
            return None
        try:
            obj_id = int(bytes(data))
        except (ValueError, TypeError):
            return None
        for mod in self._modules:
            if id(mod) == obj_id:
                return mod
        return None

    def _drop_index_for_pos(self, pos_y: int) -> int:
        """Compute the insertion index from a y coordinate in inner widget space."""
        for i, mod in enumerate(self._modules):
            mid_y = mod.y() + mod.height() // 2
            if pos_y < mid_y:
                return i
        return len(self._modules)

    def _inner_dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-rack-module-id"):
            mod = self._module_for_mime(event.mimeData())
            if mod is not None:
                event.acceptProposedAction()
                return
        event.ignore()

    def _inner_dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-rack-module-id"):
            mod = self._module_for_mime(event.mimeData())
            if mod is not None:
                event.acceptProposedAction()
                self._drop_indicator_idx = self._drop_index_for_pos(
                    int(event.position().y()))
                self._inner.update()
                return
        event.ignore()

    def _inner_dragLeaveEvent(self, event):
        self._drop_indicator_idx = -1
        self._inner.update()
        event.accept()

    def _inner_dropEvent(self, event):
        self._drop_indicator_idx = -1
        self._inner.update()
        if not event.mimeData().hasFormat("application/x-rack-module-id"):
            event.ignore()
            return
        mod = self._module_for_mime(event.mimeData())
        if mod is None:
            event.ignore()
            return
        from_idx = self._modules.index(mod)
        to_idx = self._drop_index_for_pos(int(event.position().y()))
        # Adjust for removal shift
        if to_idx > from_idx:
            to_idx = min(to_idx - 1, len(self._modules) - 1)
        self.move_module(from_idx, to_idx)
        event.acceptProposedAction()

    def _inner_paintEvent(self, event):
        """Draw the inner widget with drop indicator overlay."""
        self._inner_orig_paint(event)
        if self._drop_indicator_idx < 0:
            return
        p = QPainter(self._inner)
        if not p.isActive():
            return
        p.setPen(QPen(QColor("#ffaa33"), 2))
        idx = self._drop_indicator_idx
        if idx < len(self._modules):
            y = self._modules[idx].y() - 1
        elif self._modules:
            last = self._modules[-1]
            y = last.y() + last.height() + 1
        else:
            y = 0
        p.drawLine(0, y, self._inner.width(), y)
        p.end()

    # --- painting: rack case frame --------------------------------

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        if not p.isActive():
            return
        w = self.width()

        # Subtle top/bottom rail shadow
        p.setPen(Qt.PenStyle.NoPen)
        grad = QLinearGradient(0, 0, 0, 6)
        grad.setColorAt(0, QColor(0, 0, 0, 80))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(grad)
        p.drawRect(0, 0, w, 6)
        p.end()
