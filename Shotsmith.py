#!/usr/bin/env python3
"""
Shotsmith — Timeline clip manager for DaVinci Resolve
======================================================
Spreadsheet-style bulk clip naming and organization tool.
All clips visible at once — filter, edit inline, batch apply, commit in one shot.

Install:  pip install PySide6
Run:      Drop in Resolve's Scripts/Utility folder, or run externally
          with Resolve open.
"""

import sys
import os
import tempfile
import traceback
import logging

# ── Log file (system temp dir — not intrusive) ────────────────────────────────
_log_path = os.path.join(tempfile.gettempdir(), "Shotsmith_log.txt")
logging.basicConfig(
    filename=_log_path,
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)
logging.info("Script started")

def _except_hook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.critical("UNCAUGHT EXCEPTION:\n%s", msg)
    # Also try to show a message box if Qt is running
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app:
            QtWidgets.QMessageBox.critical(None, "Shotsmith Crashed", msg)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _except_hook

def _safe(fn):
    """Decorator — catches and logs any exception inside a Qt callback."""
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            msg = traceback.format_exc()
            logging.critical("CRASH in %s:\n%s", fn.__qualname__, msg)
            try:
                from PySide6 import QtWidgets
                QtWidgets.QMessageBox.critical(
                    None, f"Error in {fn.__name__}", msg
                )
            except Exception:
                pass
    return wrapper

# ── Resolve scripting module ──────────────────────────────────────────────────
try:
    import DaVinciResolveScript as dvr
except ImportError:
    PATHS = [
        os.path.join(
            os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
            r"Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules",
        ),
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
        "Developer/Scripting/Modules/",
        "/opt/resolve/Developer/Scripting/Modules/",
    ]
    for p in PATHS:
        if os.path.exists(os.path.join(p, "DaVinciResolveScript.py")):
            sys.path.append(p)
            break
    else:
        logging.critical("DaVinciResolveScript.py not found in any standard path")
    import DaVinciResolveScript as dvr

# ── GUI ───────────────────────────────────────────────────────────────────────
try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt, QSortFilterProxyModel
    from PySide6.QtGui import QShortcut, QKeySequence, QColor, QFont
except ImportError as e:
    logging.critical("PySide6 import failed: %s", e)
    print("PySide6 not installed.  Run: pip install PySide6")
    sys.exit(1)

# ── Resolve connection ────────────────────────────────────────────────────────
resolve = dvr.scriptapp("Resolve")
if resolve is None:
    msg = (
        "Could not connect to DaVinci Resolve.\n\n"
        "Check that:\n"
        "  • Resolve is running\n"
        "  • Preferences → System → General → External scripting using = Local\n"
        "  • You're running this from inside Resolve (Workspace → Scripts → "
        "Utility → VFX_Clip_Renamer), not as a standalone Python script "
        "(unless your env vars are set up for external scripting)."
    )
    logging.critical(msg)
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(None, "Shotsmith — Resolve Not Found", msg)
    except Exception:
        print(msg)
    sys.exit(1)

pm       = resolve.GetProjectManager()
project  = pm.GetCurrentProject() if pm else None
timeline = project.GetCurrentTimeline() if project else None

# ── Constants ─────────────────────────────────────────────────────────────────
# Clip color highlights (must be valid Resolve clip color names)
HL_ACTIVE  = "Yellow"
HL_NAMED   = "Teal"
HL_DEFAULT = "Blue"

# ── Palette ───────────────────────────────────────────────────────────────────
PAL = {
    "bg":        "#171720",
    "surface":   "#1E1E2C",
    "panel":     "#252535",
    "border":    "#333350",
    "accent":    "#CC00CC",
    "accent2":   "#7B2FBE",
    "fg":        "#E0E0F0",
    "fg_dim":    "#7878A0",
    "fg_muted":  "#454560",
    "green":     "#26A65B",
    "red":       "#E53935",
    "yellow":    "#F9A825",
    "row_alt":   "#1A1A28",
    "row_sel":   "#2A1A3A",
    "header_bg": "#0F0F1A",
}


# ═════════════════════════════════════════════════════════════════════════════
# Resolve helpers
# ═════════════════════════════════════════════════════════════════════════════

def frames_to_tc(frames, fps=24):
    ff = int(frames % fps)
    s  = int((frames // fps) % 60)
    m  = int((frames // (fps * 60)) % 60)
    h  = int(frames // (fps * 3600))
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def get_clips():
    """Return sorted list of (clip_item, original_name, tc_start, duration, track)."""
    if not timeline:
        return []
    clips = []
    track_count = timeline.GetTrackCount("video")
    for t in range(1, track_count + 1):
        items = timeline.GetItemsInTrack("video", t)
        for clip in items.values():
            try:
                enabled = clip.GetClipEnabled() if hasattr(clip, "GetClipEnabled") else True
                if enabled:
                    # Fetch original source filename from media pool
                    src_file = ""
                    try:
                        mpi = clip.GetMediaPoolItem()
                        if mpi:
                            props    = mpi.GetClipProperty()
                            src_file = props.get("File Name", "")
                    except Exception:
                        pass
                    clips.append({
                        "item":        clip,
                        "name":        clip.GetName(),
                        "new_name":    clip.GetName(),
                        "source_file": src_file,
                        "tc":          frames_to_tc(clip.GetStart()),
                        "frames":      clip.GetEnd() - clip.GetStart(),
                        "track":       t,
                        "renamed":     False,
                    })
            except Exception:
                pass
    clips.sort(key=lambda c: c["item"].GetStart())
    return clips


# ═════════════════════════════════════════════════════════════════════════════
# Table model
# ═════════════════════════════════════════════════════════════════════════════

COLS = ["✓", "#", "SHOT ID (NEW)", "SOURCE FILE", "NEW NAME", "TIMECODE", "FRAMES", "TRACK"]
COL_CHK    = 0
COL_IDX    = 1
COL_ORIG   = 2
COL_SRC    = 3
COL_NEW    = 4
COL_TC     = 5
COL_FRAMES = 6
COL_TRACK  = 7


class ClipModel(QtCore.QAbstractTableModel):
    def __init__(self, clips):
        super().__init__()
        self.clips = clips

    def rowCount(self, _=None):    return len(self.clips)
    def columnCount(self, _=None): return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return COLS[section]
            if role == Qt.CheckStateRole and section == COL_CHK:
                # Header checkbox reflects all-checked state of visible clips
                return Qt.Unchecked   # managed by CheckableHeader
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        clip = self.clips[row]

        if role == Qt.CheckStateRole and col == COL_CHK:
            return Qt.Checked if clip.get("checked", False) else Qt.Unchecked

        if role == Qt.DisplayRole:
            if col == COL_CHK:    return None
            if col == COL_IDX:    return str(row + 1)
            if col == COL_ORIG:   return clip["name"]
            if col == COL_SRC:    return clip.get("source_file", "")
            if col == COL_NEW:    return clip["new_name"]
            if col == COL_TC:     return clip["tc"]
            if col == COL_FRAMES: return str(clip["frames"])
            if col == COL_TRACK:  return str(clip["track"])

        if role == Qt.EditRole and col == COL_NEW:
            return clip["new_name"]

        if role == Qt.ForegroundRole:
            if col == COL_NEW:
                if clip["renamed"]:
                    return QColor(PAL["green"])
                if clip["new_name"] != clip["name"]:
                    return QColor(PAL["accent"])
            if col in (COL_ORIG, COL_SRC):
                return QColor(PAL["fg_dim"])
            if col in (COL_TC, COL_FRAMES, COL_TRACK):
                return QColor(PAL["fg_muted"])
            return QColor(PAL["fg"])

        if role == Qt.BackgroundRole:
            if clip.get("checked", False):
                return QColor(PAL["row_sel"])
            if row % 2 == 0:
                return QColor(PAL["surface"])
            return QColor(PAL["row_alt"])

        if role == Qt.TextAlignmentRole:
            if col in (COL_IDX, COL_TC, COL_FRAMES, COL_TRACK, COL_CHK):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.FontRole:
            f = QFont("SF Mono, Menlo, Consolas, monospace", 9)
            if col == COL_NEW and not clip["renamed"]:
                f.setBold(True)
            return f

        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_CHK:
            return base   # toggling handled exclusively by _on_cell_clicked
        if index.column() == COL_NEW:
            return base | Qt.ItemIsEditable
        return base

    def setData(self, index, value, role=Qt.EditRole):
        row = index.row()
        col = index.column()
        if col == COL_CHK and role == Qt.CheckStateRole:
            self.clips[row]["checked"] = (value == Qt.Checked)
            self.dataChanged.emit(
                self.index(row, 0),
                self.index(row, self.columnCount() - 1)
            )
            return True
        if col == COL_NEW and role == Qt.EditRole:
            self.clips[row]["new_name"] = value.strip()
            self.clips[row]["renamed"]  = False
            self.dataChanged.emit(index, index)
            return True
        return False

    def check_all(self, checked, rows=None):
        """Check/uncheck all rows, or a specific list of source rows."""
        targets = rows if rows is not None else range(len(self.clips))
        for i in targets:
            self.clips[i]["checked"] = checked
        self.beginResetModel()
        self.endResetModel()

    def checked_rows(self):
        return {i for i, c in enumerate(self.clips) if c.get("checked", False)}


# ═════════════════════════════════════════════════════════════════════════════
# Checkable header
# ═════════════════════════════════════════════════════════════════════════════

class CheckableHeader(QtWidgets.QHeaderView):
    toggle_all = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._checked = False
        self.setSectionsClickable(True)
        self.sectionClicked.connect(self._on_section_clicked)

    def _on_section_clicked(self, section):
        if section == COL_CHK:
            self._checked = not self._checked
            self.toggle_all.emit(self._checked)
            self.viewport().update()

    def paintSection(self, painter, rect, section):
        super().paintSection(painter, rect, section)
        if section == COL_CHK:
            opt = QtWidgets.QStyleOptionButton()
            cb_size = 13
            opt.rect = QtCore.QRect(
                rect.x() + (rect.width() - cb_size) // 2,
                rect.y() + (rect.height() - cb_size) // 2,
                cb_size, cb_size
            )
            opt.state = (
                QtWidgets.QStyle.State_Enabled |
                (QtWidgets.QStyle.State_On if self._checked
                 else QtWidgets.QStyle.State_Off)
            )
            self.style().drawControl(
                QtWidgets.QStyle.CE_CheckBox, opt, painter)

    def set_checked(self, state):
        self._checked = state
        self.viewport().update()


# ═════════════════════════════════════════════════════════════════════════════
# Main window
# ═════════════════════════════════════════════════════════════════════════════

TRANSITION_KEYWORDS = ("dissolve", "fade", "wipe", "iris", "push", "slide")


def is_transition(clip):
    name = (clip.get("name") or "").lower()
    return any(k in name for k in TRANSITION_KEYWORDS)


class TrackFilterProxy(QSortFilterProxyModel):
    """Proxy that filters by text search, track numbers, and transition visibility."""
    def __init__(self, clips):
        super().__init__()
        self._clips             = clips
        self._active_tracks     = set()   # empty = show all
        self._text              = ""
        self._hide_transitions  = True

    def set_text(self, text):
        self._text = text.lower()
        self.invalidate()

    def set_active_tracks(self, tracks: set):
        self._active_tracks = tracks
        self.invalidate()

    def set_hide_transitions(self, hide: bool):
        self._hide_transitions = hide
        self.invalidate()

    def filterAcceptsRow(self, source_row, source_parent):
        clips = self.sourceModel().clips
        if source_row >= len(clips):
            return True
        clip = clips[source_row]

        # Transition filter
        if self._hide_transitions and is_transition(clip):
            return False

        # Track filter
        if self._active_tracks and clip["track"] not in self._active_tracks:
            return False

        # Text filter (searches original name and new name)
        if self._text:
            haystack = (clip["name"] + clip["new_name"] + clip.get("source_file","")).lower()
            if self._text not in haystack:
                return False

        return True


class ClipRenamer(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.clips          = get_clips()
        self.model          = ClipModel(self.clips)
        self._track_btns    = {}          # track_num -> QPushButton
        self._active_tracks = set()       # empty = all visible
        self._last_colors   = {}          # clip index -> last color set
        self._build()
        self._style()
        self.setWindowTitle("Shotsmith")
        self.resize(960, 660)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self._rebuild_track_buttons()
        self._refresh_status()

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────────
        title_bar = QtWidgets.QWidget()
        title_bar.setFixedHeight(48)
        title_bar.setObjectName("titleBar")
        tb_layout = QtWidgets.QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(18, 0, 18, 0)

        lbl_title = QtWidgets.QLabel("SHOTSMITH")
        lbl_title.setObjectName("titleLabel")

        self.lbl_info = QtWidgets.QLabel("")
        self.lbl_info.setObjectName("infoLabel")

        tb_layout.addWidget(lbl_title)
        tb_layout.addStretch()
        tb_layout.addWidget(self.lbl_info)
        root.addWidget(title_bar)

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = QtWidgets.QWidget()
        toolbar.setFixedHeight(52)
        toolbar.setObjectName("toolbar")
        tb = QtWidgets.QHBoxLayout(toolbar)
        tb.setContentsMargins(14, 6, 14, 6)
        tb.setSpacing(8)

        # Search
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter clips…")
        self.search.setFixedWidth(180)
        self.search.setObjectName("searchBox")
        self.search.textChanged.connect(self._filter)

        # Prefix / Suffix
        lbl_pre = QtWidgets.QLabel("PREFIX")
        lbl_pre.setObjectName("toolLabel")
        self.prefix = QtWidgets.QLineEdit()
        self.prefix.setPlaceholderText("e.g.  R1_")
        self.prefix.setFixedWidth(100)

        lbl_suf = QtWidgets.QLabel("SUFFIX")
        lbl_suf.setObjectName("toolLabel")
        self.suffix = QtWidgets.QLineEdit()
        self.suffix.setPlaceholderText("e.g.  _v01")
        self.suffix.setFixedWidth(100)

        # Base name
        lbl_base = QtWidgets.QLabel("BASE")
        lbl_base.setObjectName("toolLabel")
        self.base_name = QtWidgets.QLineEdit()
        self.base_name.setPlaceholderText("e.g.  VFX")
        self.base_name.setFixedWidth(90)

        # Number start
        lbl_num = QtWidgets.QLabel("START #")
        lbl_num.setObjectName("toolLabel")
        self.num_start = QtWidgets.QSpinBox()
        self.num_start.setValue(10)
        self.num_start.setRange(0, 9999)
        self.num_start.setFixedWidth(60)

        # Pad digits
        lbl_pad = QtWidgets.QLabel("PAD")
        lbl_pad.setObjectName("toolLabel")
        self.num_pad = QtWidgets.QSpinBox()
        self.num_pad.setValue(3)
        self.num_pad.setRange(1, 6)
        self.num_pad.setFixedWidth(50)

        lbl_step = QtWidgets.QLabel("STEP")
        lbl_step.setObjectName("toolLabel")
        self.num_step = QtWidgets.QSpinBox()
        self.num_step.setValue(10)
        self.num_step.setRange(1, 1000)
        self.num_step.setFixedWidth(55)
        self.num_step.setToolTip("Increment step — e.g. 10 gives 10, 20, 30…")

        # Buttons
        self.btn_preview  = self._btn("PREVIEW",  self._preview,  "accentBtn")
        self.btn_apply_sel= self._btn("APPLY SEL", self._apply_selected)
        self.btn_commit   = self._btn("COMMIT ALL", self._commit,  "commitBtn")
        self.btn_rescan   = self._btn("↺ RESCAN",  self._rescan)
        self.btn_reset    = self._btn("RESET",     self._reset)
        self.btn_export    = self._btn("↓ CSV",       self._export_csv,         "exportBtn")
        self.btn_highlight = self._btn("◎ HIGHLIGHT", self._highlight_selected, "highlightBtn")
        self.btn_highlight.setToolTip("Highlight selected clips in Resolve timeline (Ctrl+H)")

        for w in [self.search, lbl_pre, self.prefix, lbl_suf, self.suffix,
                  lbl_base, self.base_name, lbl_num, self.num_start,
                  lbl_pad, self.num_pad, lbl_step, self.num_step]:
            tb.addWidget(w)
        tb.addStretch()
        for w in [self.btn_preview, self.btn_apply_sel,
                  self.btn_commit, self.btn_rescan, self.btn_reset,
                  self.btn_export, self.btn_highlight]:
            tb.addWidget(w)

        root.addWidget(toolbar)

        # ── Divider ───────────────────────────────────────────────────────────
        div = QtWidgets.QFrame()
        div.setFrameShape(QtWidgets.QFrame.HLine)
        div.setObjectName("divider")
        root.addWidget(div)

        # ── Track filter bar ──────────────────────────────────────────────────
        self.track_bar = QtWidgets.QWidget()
        self.track_bar.setFixedHeight(38)
        self.track_bar.setObjectName("trackBar")
        self.track_layout = QtWidgets.QHBoxLayout(self.track_bar)
        self.track_layout.setContentsMargins(14, 4, 14, 4)
        self.track_layout.setSpacing(6)

        lbl_tracks = QtWidgets.QLabel("TRACKS")
        lbl_tracks.setObjectName("toolLabel")
        self.track_layout.addWidget(lbl_tracks)

        self.btn_all_tracks = QtWidgets.QPushButton("ALL")
        self.btn_all_tracks.setObjectName("trackBtnAll")
        self.btn_all_tracks.setFixedSize(40, 24)
        self.btn_all_tracks.setCheckable(True)
        self.btn_all_tracks.setChecked(True)
        self.btn_all_tracks.clicked.connect(lambda _: self._select_all_tracks())
        self.track_layout.addWidget(self.btn_all_tracks)

        # Hide transitions toggle (placed before per-track buttons so it stays visible)
        self.btn_hide_trans = QtWidgets.QPushButton("SHOW TRANSITIONS")
        self.btn_hide_trans.setObjectName("trackBtn")
        self.btn_hide_trans.setFixedHeight(24)
        self.btn_hide_trans.setCheckable(True)
        self.btn_hide_trans.setChecked(True)
        self.btn_hide_trans.setToolTip(
            "Hide cross dissolves and other transitions "
            f"(matched by name: {', '.join(TRANSITION_KEYWORDS)})"
        )
        self.btn_hide_trans.toggled.connect(self._on_hide_transitions)
        self.track_layout.addWidget(self.btn_hide_trans)

        # Per-track buttons added dynamically by _rebuild_track_buttons()
        self.track_layout.addStretch()
        root.addWidget(self.track_bar)

        div2 = QtWidgets.QFrame()
        div2.setFrameShape(QtWidgets.QFrame.HLine)
        div2.setObjectName("divider")
        root.addWidget(div2)

        # ── Table ─────────────────────────────────────────────────────────────
        self.proxy = TrackFilterProxy(self.clips)
        self.proxy.setSourceModel(self.model)

        self.table = QtWidgets.QTableView()
        self.table.setModel(self.proxy)
        self.table.setObjectName("clipTable")
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked |
            QtWidgets.QAbstractItemView.SelectedClicked |
            QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setStretchLastSection(False)

        # Column widths set after header replacement below

        # Checkable header — replaces default horizontal header
        self._chk_header = CheckableHeader(self.table)
        self._chk_header.toggle_all.connect(lambda checked: self._toggle_all_visible(checked))
        self.table.setHorizontalHeader(self._chk_header)
        # Re-apply column widths after replacing header
        hdr2 = self.table.horizontalHeader()
        hdr2.resizeSection(COL_CHK,    28)
        hdr2.resizeSection(COL_IDX,    40)
        hdr2.resizeSection(COL_ORIG,  160)
        hdr2.resizeSection(COL_SRC,   180)
        hdr2.resizeSection(COL_TC,    100)
        hdr2.resizeSection(COL_FRAMES, 65)
        hdr2.resizeSection(COL_TRACK,  50)
        hdr2.setSectionResizeMode(COL_NEW, QtWidgets.QHeaderView.Stretch)
        hdr2.setHighlightSections(False)
        hdr2.setStretchLastSection(False)

        # Single click on checkbox column toggles check; elsewhere is normal
        self.table.clicked.connect(self._on_cell_clicked)
        self.table.setItemDelegateForColumn(COL_NEW, NewNameDelegate(self))
        # selection signal intentionally not connected — highlight is opt-in

        root.addWidget(self.table)

        # ── Status bar ────────────────────────────────────────────────────────
        status_bar = QtWidgets.QWidget()
        status_bar.setFixedHeight(28)
        status_bar.setObjectName("statusBar")
        sb = QtWidgets.QHBoxLayout(status_bar)
        sb.setContentsMargins(14, 0, 14, 0)

        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setObjectName("statusLabel")

        lbl_keys = QtWidgets.QLabel(
            "Enter / Tab = confirm edit   ·   Del = uncheck all   ·"
            "   Click ✓ column to check/uncheck   ·   Header ✓ = toggle all visible   ·   Ctrl+H = highlight in Resolve"
        )
        lbl_keys.setObjectName("keysLabel")

        sb.addWidget(self.lbl_status)
        sb.addStretch()
        sb.addWidget(lbl_keys)
        root.addWidget(status_bar)

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        QShortcut(QKeySequence("Ctrl+R"),      self, activated=self._rescan)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._commit)
        QShortcut(QKeySequence("Ctrl+P"),      self, activated=self._preview)
        QShortcut(QKeySequence("Delete"),      self, activated=self._clear_checked)
        QShortcut(QKeySequence("Ctrl+E"),      self, activated=self._export_csv)
        QShortcut(QKeySequence("Ctrl+H"),      self, activated=self._highlight_selected)

    def _btn(self, label, slot, obj_name="toolBtn"):
        b = QtWidgets.QPushButton(label)
        b.setObjectName(obj_name)
        b.clicked.connect(lambda checked=False, s=slot: s())
        b.setFixedHeight(32)
        return b

    # ── Stylesheet ────────────────────────────────────────────────────────────
    def _style(self):
        self.setStyleSheet(f"""
        QWidget {{
            background: {PAL['bg']};
            color: {PAL['fg']};
            font-family: 'SF Pro Display', 'Segoe UI', 'Helvetica Neue', sans-serif;
            font-size: 11px;
        }}

        #titleBar {{
            background: {PAL['header_bg']};
            border-bottom: 1px solid {PAL['accent']};
        }}
        #titleLabel {{
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 3px;
            color: {PAL['accent']};
        }}
        #infoLabel {{
            font-size: 10px;
            color: {PAL['fg_dim']};
            letter-spacing: 1px;
        }}

        #toolbar {{
            background: {PAL['panel']};
        }}
        #toolLabel {{
            font-size: 9px;
            font-weight: 700;
            letter-spacing: 2px;
            color: {PAL['fg_muted']};
            margin-left: 6px;
        }}
        #divider {{
            color: {PAL['border']};
            background: {PAL['border']};
            border: none;
            max-height: 1px;
        }}

        QLineEdit {{
            background: {PAL['surface']};
            border: 1px solid {PAL['border']};
            border-radius: 4px;
            padding: 4px 8px;
            color: {PAL['fg']};
            font-size: 11px;
            selection-background-color: {PAL['accent']};
        }}
        QLineEdit:focus {{
            border-color: {PAL['accent']};
        }}
        #searchBox {{
            border-radius: 12px;
            padding: 4px 12px;
        }}

        QSpinBox {{
            background: {PAL['surface']};
            border: 1px solid {PAL['border']};
            border-radius: 4px;
            padding: 4px 6px;
            color: {PAL['fg']};
        }}
        QSpinBox:focus {{ border-color: {PAL['accent']}; }}
        QSpinBox::up-button, QSpinBox::down-button {{
            background: {PAL['panel']};
            border: none;
            width: 14px;
        }}

        QPushButton {{
            border: 1px solid {PAL['border']};
            border-radius: 4px;
            padding: 0 12px;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1px;
            background: {PAL['surface']};
            color: {PAL['fg']};
        }}
        QPushButton:hover {{ background: {PAL['panel']}; border-color: {PAL['fg_dim']}; }}
        QPushButton:pressed {{ background: {PAL['border']}; }}

        #accentBtn {{
            background: {PAL['accent2']};
            border-color: {PAL['accent']};
            color: white;
        }}
        #accentBtn:hover {{ background: {PAL['accent']}; }}

        #commitBtn {{
            background: {PAL['green']};
            border-color: {PAL['green']};
            color: white;
            font-weight: 700;
        }}
        #commitBtn:hover {{ background: #2ECC71; border-color: #2ECC71; }}

        #exportBtn {{
            background: {PAL['surface']};
            border: 1px solid {PAL['border']};
            border-radius: 4px;
            padding: 0 12px;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1px;
            color: {PAL['fg_dim']};
        }}
        #exportBtn:hover {{
            border-color: {PAL['yellow']};
            color: {PAL['yellow']};
        }}

        #highlightBtn {{
            background: {PAL['surface']};
            border: 1px solid {PAL['border']};
            border-radius: 4px;
            padding: 0 12px;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1px;
            color: {PAL['fg_dim']};
        }}
        #highlightBtn:hover {{
            border-color: {PAL['yellow']};
            color: {PAL['yellow']};
            background: #1A1A10;
        }}
        #highlightBtn:pressed {{
            background: #2A2A10;
            color: {PAL['yellow']};
        }}
        #trackBar {{
            background: {PAL['panel']};
        }}
        #trackBtn {{
            background: {PAL['surface']};
            border: 1px solid {PAL['border']};
            border-radius: 3px;
            padding: 0 10px;
            font-size: 9px;
            font-weight: 600;
            letter-spacing: 1px;
            color: {PAL['fg_dim']};
            min-width: 52px;
        }}
        #trackBtn:hover {{
            border-color: {PAL['accent']};
            color: {PAL['fg']};
        }}
        #trackBtn:checked {{
            background: {PAL['accent2']};
            border-color: {PAL['accent']};
            color: white;
        }}
        #trackBtnAll {{
            background: {PAL['surface']};
            border: 1px solid {PAL['fg_muted']};
            border-radius: 3px;
            padding: 0 10px;
            font-size: 9px;
            font-weight: 700;
            letter-spacing: 1px;
            color: {PAL['fg_dim']};
            min-width: 36px;
        }}
        #trackBtnAll:checked {{
            background: {PAL['fg_muted']};
            border-color: {PAL['fg_dim']};
            color: {PAL['fg']};
        }}

        #clipTable {{
            background: {PAL['surface']};
            alternate-background-color: {PAL['row_alt']};
            border: none;
            gridline-color: {PAL['border']};
            selection-background-color: {PAL['row_sel']};
            selection-color: {PAL['fg']};
            outline: 0;
        }}
        QHeaderView::section {{
            background: {PAL['header_bg']};
            color: {PAL['fg_dim']};
            font-size: 9px;
            font-weight: 700;
            letter-spacing: 2px;
            border: none;
            border-bottom: 1px solid {PAL['border']};
            border-right: 1px solid {PAL['border']};
            padding: 6px 8px;
        }}
        QHeaderView::section:hover {{ color: {PAL['fg']}; }}
        QScrollBar:vertical {{
            background: {PAL['bg']};
            width: 8px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {PAL['border']};
            border-radius: 4px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {PAL['fg_muted']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

        #statusBar {{
            background: {PAL['header_bg']};
            border-top: 1px solid {PAL['border']};
        }}
        #statusLabel {{
            font-size: 10px;
            color: {PAL['green']};
            font-weight: 600;
        }}
        #keysLabel {{
            font-size: 9px;
            color: {PAL['fg_muted']};
            letter-spacing: 0.5px;
        }}
        """)

    # ── Logic ─────────────────────────────────────────────────────────────────

    @_safe
    def _filter(self, text):
        self.proxy.set_text(text)

    @_safe
    def _rebuild_track_buttons(self):
        """Rebuild per-track toggle buttons based on current clip data."""
        # Layout structure (fixed prefix): [TRACKS label, ALL btn, HIDE_TRANS btn]
        # then dynamic per-track buttons, then stretch.
        FIXED_PREFIX = 3  # label + ALL + HIDE_TRANS
        # Remove old track buttons (everything after the fixed prefix, except stretch)
        while self.track_layout.count() > FIXED_PREFIX + 1:  # +1 for stretch
            item = self.track_layout.takeAt(FIXED_PREFIX)
            if item.widget():
                item.widget().deleteLater()

        # Remove stretch too, we'll re-add at end
        stretch_item = self.track_layout.takeAt(FIXED_PREFIX)

        self._track_btns = {}
        tracks = sorted(set(c["track"] for c in self.clips))
        clip_counts = {}
        for c in self.clips:
            clip_counts[c["track"]] = clip_counts.get(c["track"], 0) + 1

        for t in tracks:
            count = clip_counts.get(t, 0)
            btn = QtWidgets.QPushButton(f"V{t}  ({count})")
            btn.setObjectName("trackBtn")
            btn.setFixedHeight(24)
            btn.setCheckable(True)
            btn.setChecked(False)   # start unchecked — ALL is active
            btn.clicked.connect(lambda checked, track=t: self._toggle_track(track, checked))
            self.track_layout.addWidget(btn)
            self._track_btns[t] = btn

        self.track_layout.addStretch()

    @_safe
    def _toggle_track(self, track, checked):
        """Toggle a single track filter button."""
        if checked:
            self._active_tracks.add(track)
            self.btn_all_tracks.setChecked(False)
        else:
            self._active_tracks.discard(track)
            # If nothing selected, revert to ALL
            if not self._active_tracks:
                self.btn_all_tracks.setChecked(True)

        self.proxy.set_active_tracks(self._active_tracks)
        self._refresh_status()

    @_safe
    def _on_hide_transitions(self, hide):
        self.proxy.set_hide_transitions(hide)
        self.btn_hide_trans.setText("SHOW TRANSITIONS" if hide else "HIDE TRANSITIONS")
        self._refresh_status()

    @_safe
    def _select_all_tracks(self):
        """Deactivate all track filters — show everything."""
        self._active_tracks = set()
        self.btn_all_tracks.setChecked(True)
        for btn in self._track_btns.values():
            btn.setChecked(False)
        self.proxy.set_active_tracks(self._active_tracks)
        self._refresh_status()

    @_safe
    def _preview(self):
        """Apply prefix/suffix/base/numbering to ALL visible rows as a preview."""
        base    = self.base_name.text().strip()
        pre     = self.prefix.text().strip()
        suf     = self.suffix.text().strip()
        start   = self.num_start.value()
        pad     = max(1, self.num_pad.value())
        step    = max(1, self.num_step.value())

        use_base = bool(base)

        for vis_row in range(self.proxy.rowCount()):
            src_idx = self.proxy.mapToSource(self.proxy.index(vis_row, 0))
            clip = self.clips[src_idx.row()]
            num  = start + vis_row * step

            if use_base:
                name = f"{pre}{base}_{num:0{pad}d}{suf}"
            else:
                # Keep original name, just wrap with prefix/suffix
                name = f"{pre}{clip['name']}{suf}"

            clip["new_name"] = name
            clip["renamed"]  = False

        self.model.dataChanged.emit(
            self.model.index(0, 0),
            self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1)
        )
        self._refresh_status()

    @_safe
    def _apply_selected(self):
        """Apply pattern (or keep manual edits) to checked rows AND commit to Resolve."""
        rows = self._checked_source_rows()
        if not rows:
            self.lbl_status.setText("No clips checked — tick checkboxes first")
            return
        base  = self.base_name.text().strip()
        pre   = self.prefix.text().strip()
        suf   = self.suffix.text().strip()
        start = int(self.num_start.value())
        pad   = max(1, int(self.num_pad.value()))
        step  = max(1, int(self.num_step.value()))

        # If all pattern fields are empty, preserve whatever the user typed in NEW NAME
        use_pattern = bool(base or pre or suf)

        try:
            for i, row in enumerate(sorted(rows)):
                if row >= len(self.clips):
                    continue
                clip = self.clips[row]
                if use_pattern:
                    num       = start + i * step
                    num_str   = str(num).zfill(pad)
                    orig_name = clip["name"] or ""
                    if base:
                        clip["new_name"] = f"{pre}{base}_{num_str}{suf}"
                    else:
                        clip["new_name"] = f"{pre}{orig_name}{suf}"
                    clip["renamed"] = False
                # else: leave clip["new_name"] alone — user typed it manually
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Apply Failed", str(e))
            return

        # Commit checked rows to Resolve
        committed = 0
        errors    = []
        for row in sorted(rows):
            if row >= len(self.clips):
                continue
            clip = self.clips[row]
            new = (clip["new_name"] or "").strip()
            if not new or new == clip["name"]:
                continue
            try:
                clip["item"].SetName(new)
                clip["item"].SetClipColor(HL_NAMED)
                clip["name"]    = new
                clip["renamed"] = True
                committed += 1
            except Exception as e:
                errors.append(f"{clip['name']}: {e}")

        self.model.beginResetModel()
        self.model.endResetModel()
        self._refresh_status()

        if errors:
            QtWidgets.QMessageBox.warning(
                self, "Apply Selected",
                f"✓  {committed} renamed\n\nErrors ({len(errors)}):\n" + "\n".join(errors)
            )
        else:
            mode = "from pattern" if use_pattern else "from manual edits"
            self.lbl_status.setText(f"✓  {committed} clip(s) renamed in Resolve ({mode})")

    @_safe
    def _commit(self):
        """Write all new names to Resolve and update clip colors."""
        changed = 0
        errors  = []
        for clip in self.clips:
            if clip["new_name"] and clip["new_name"] != clip["name"]:
                try:
                    clip["item"].SetName(clip["new_name"])
                    clip["item"].SetClipColor(HL_NAMED)
                    clip["name"]    = clip["new_name"]
                    clip["renamed"] = True
                    changed += 1
                except Exception as e:
                    errors.append(f"{clip['name']}: {e}")

        self.model.beginResetModel()
        self.model.endResetModel()
        self._refresh_status()

        msg = f"✓  {changed} clip(s) renamed in Resolve."
        if errors:
            msg += f"\n\nErrors ({len(errors)}):\n" + "\n".join(errors)
            QtWidgets.QMessageBox.warning(self, "Commit", msg)
        else:
            self.lbl_status.setText(f"✓  {changed} clips committed to Resolve")

    @_safe
    def _rescan(self):
        self.clips = get_clips()
        self.model.clips = self.clips
        self.proxy._clips = self.clips
        self._active_tracks = set()
        self._last_colors   = {}
        self.model.beginResetModel()
        self.model.endResetModel()
        self._rebuild_track_buttons()
        self._select_all_tracks()
        self._chk_header.set_checked(False)
        self._refresh_status()

    @_safe
    def _reset(self):
        """Reset all new names back to original."""
        self.table.setUpdatesEnabled(False)
        try:
            for clip in self.clips:
                clip["new_name"] = clip["name"]
                clip["renamed"]  = False
        finally:
            self.table.setUpdatesEnabled(True)
        self.model.beginResetModel()
        self.model.endResetModel()
        self._refresh_status()

    @_safe
    def _on_cell_clicked(self, proxy_index):
        """Toggle checkbox when clicking anywhere in the checkbox column."""
        if proxy_index.column() == COL_CHK:
            src_idx = self.proxy.mapToSource(proxy_index)
            clip = self.clips[src_idx.row()]
            clip["checked"] = not clip.get("checked", False)
            self.model.dataChanged.emit(
                self.model.index(src_idx.row(), 0),
                self.model.index(src_idx.row(), self.model.columnCount() - 1)
            )
            self._sync_header_checkbox()
            self._refresh_status()

    @_safe
    def _toggle_all_visible(self, checked):
        """Check/uncheck all currently visible (filtered) rows."""
        self.table.setUpdatesEnabled(False)
        try:
            for vis_row in range(self.proxy.rowCount()):
                src_idx = self.proxy.mapToSource(self.proxy.index(vis_row, 0))
                self.clips[src_idx.row()]["checked"] = checked
        finally:
            self.table.setUpdatesEnabled(True)
        self.model.beginResetModel()
        self.model.endResetModel()
        self._chk_header.set_checked(checked)
        self._refresh_status()

    @_safe
    def _sync_header_checkbox(self):
        """Keep header checkbox in sync with row states."""
        visible_rows = [
            self.proxy.mapToSource(self.proxy.index(r, 0)).row()
            for r in range(self.proxy.rowCount())
        ]
        if not visible_rows:
            return
        all_checked = all(self.clips[r].get("checked", False) for r in visible_rows)
        self._chk_header.set_checked(all_checked)

    def _checked_source_rows(self):
        """Return source row indices of all checked clips."""
        return self.model.checked_rows()

    @_safe
    def _clear_checked(self):
        """Uncheck all rows."""
        self.table.setUpdatesEnabled(False)
        try:
            for clip in self.clips:
                clip["checked"] = False
        finally:
            self.table.setUpdatesEnabled(True)
        self.model.beginResetModel()
        self.model.endResetModel()
        self._chk_header.set_checked(False)
        self._refresh_status()

    @_safe
    def _highlight_selected(self):
        """Highlight checked clips in Resolve timeline."""
        rows = self._checked_source_rows()
        if not rows:
            self.lbl_status.setText("No clips checked — tick checkboxes first")
            return
        for i, clip in enumerate(self.clips):
            if i in rows:
                desired = HL_ACTIVE
            elif clip["renamed"]:
                desired = HL_NAMED
            else:
                desired = HL_DEFAULT
            if self._last_colors.get(i) != desired:
                try:
                    clip["item"].SetClipColor(desired)
                    self._last_colors[i] = desired
                except Exception:
                    pass
        self.lbl_status.setText(f"◎  {len(rows)} clip(s) highlighted in timeline")

    @_safe
    def _export_csv(self):
        """Export currently visible (filtered) rows to a CSV file."""
        import csv
        from PySide6.QtWidgets import QFileDialog
        import datetime

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSV",
            f"vfx_clips_{datetime.date.today().isoformat()}.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return

        # Gather visible rows from proxy (respects track + text filters)
        rows = []
        for vis_row in range(self.proxy.rowCount()):
            src_idx = self.proxy.mapToSource(self.proxy.index(vis_row, 0))
            clip = self.clips[src_idx.row()]
            rows.append(clip)

        headers = ["#", "SHOT ID", "SOURCE FILE", "NEW NAME", "TIMECODE", "FRAMES", "TRACK", "RENAMED"]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                for i, clip in enumerate(rows):
                    writer.writerow({
                        "#":           i + 1,
                        "SHOT ID":     clip["name"],
                        "SOURCE FILE": clip.get("source_file", ""),
                        "NEW NAME":    clip["new_name"],
                        "TIMECODE":    clip["tc"],
                        "FRAMES":      clip["frames"],
                        "TRACK":       clip["track"],
                        "RENAMED":     "Yes" if clip["renamed"] else "",
                    })
            self.lbl_status.setText(
                f"✓  Exported {len(rows)} rows to {path.split('/')[-1].split(chr(92))[-1]}"
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Export Failed", str(e))

    @_safe
    def _refresh_status(self):
        total    = len(self.clips)
        pending  = sum(1 for c in self.clips if c["new_name"] != c["name"] and not c["renamed"])
        renamed  = sum(1 for c in self.clips if c["renamed"])
        checked  = sum(1 for c in self.clips if c.get("checked", False))
        tl_name  = timeline.GetName() if timeline else "—"
        checked_str = f"   ·   {checked} checked" if checked else ""
        self.lbl_info.setText(
            f"{tl_name}   ·   {total} clips   ·   "
            f"{pending} pending   ·   {renamed} committed{checked_str}"
        )
        if pending > 0:
            self.lbl_status.setText(f"{pending} names staged — press COMMIT ALL to apply")
        elif renamed > 0:
            self.lbl_status.setText(f"✓  {renamed} clips committed to Resolve")
        else:
            self.lbl_status.setText("No changes pending")


# ═════════════════════════════════════════════════════════════════════════════
# Custom delegate for NEW NAME column
# ═════════════════════════════════════════════════════════════════════════════

class NewNameDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QtWidgets.QLineEdit(parent)
        editor.setStyleSheet(f"""
            QLineEdit {{
                background: #2A1A3A;
                color: #FFFFFF;
                border: 1px solid {PAL['accent']};
                border-radius: 2px;
                padding: 2px 6px;
                font-size: 11px;
                font-weight: bold;
                selection-background-color: {PAL['accent']};
            }}
        """)
        return editor

    def paint(self, painter, option, index):
        # Highlight cells with pending edits — guard against model type mismatches
        try:
            model = index.model()
            # index.model() may be the proxy or the source — handle both
            if hasattr(model, "mapToSource"):
                src_index = model.mapToSource(index)
                source_model = model.sourceModel()
            else:
                src_index = index
                source_model = model
            clip_idx = src_index.row()
            clips = getattr(source_model, "clips", [])
            if 0 <= clip_idx < len(clips):
                clip = clips[clip_idx]
                if clip["new_name"] != clip["name"] and not clip["renamed"]:
                    painter.fillRect(option.rect, QColor("#2A1A3A"))
        except Exception:
            pass  # never crash during paint
        super().paint(painter, option, index)


# ═════════════════════════════════════════════════════════════════════════════
# Run
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        logging.info("Creating QApplication")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        logging.info("Building window")
        win = ClipRenamer()
        logging.info("Showing window")
        win.show()
        logging.info("Entering event loop")
        app.exec()
        logging.info("Event loop exited")
    except Exception:
        msg = traceback.format_exc()
        logging.critical("STARTUP CRASH:\n%s", msg)
        try:
            QtWidgets.QMessageBox.critical(None, "Shotsmith Crashed", msg)
        except Exception:
            pass
        raise
