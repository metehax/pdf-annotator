"""
PDF İşaretleme / Not Alma Aracı  (v3)
======================================

Nesne yönelimli, obje-tabanlı bir PDF annotation uygulaması.

Mimari:
    - Annotation model ailesi : Text/Ink/Line/Shape/Highlight/Redaction/Number.
      Koordinatlar PDF puntosu cinsinden. Her nesne create_item (Qt sahnesi),
      apply_to_page (fitz'e kaydetme) ve translate (fareyle taşıma) bilir.
    - PDFDocument      : PyMuPDF sarmalayıcısı — render, metin satırı çıkarımı,
      PDF/görsel dışa aktarım, sayfa ekleme/silme/kopyalama/taşıma.
    - DocumentSession  : Açık bir PDF'in TÜM durumu (belge + işaretler + geçmiş
      + aktif sayfa). Aynı anda birden fazla PDF açık tutmak için MainWindow
      bir DocumentSession listesi tutar; "aktif" olan ekranda gösterilir.
    - HistoryManager   : Bir session'a bağlı Geri Al / Yinele yığını.
    - PDFView          : Sahne birimi = PDF puntosu. Araçlara göre fare
      olaylarını yönetir (çizim / taşıma / sağ-tık menüsü).
    - ComparePane      : Karşılaştırma modunda kullanılan, salt-okunur,
      kendi belge/sayfa seçimine sahip küçük görüntüleyici.
    - MainWindow       : Üst çubuk, sol Belgeler+Sayfalar paneli, orta
      canvas (normal / karşılaştırma), sağ Özellikler+Katmanlar paneli.

Bağımlılıklar:
    pip install PySide6 PyMuPDF
"""

import copy
import math
import os
import sys

import pymupdf as fitz

from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QSize, QUrl
from PySide6.QtGui import (
    QAction, QBrush, QColor, QDesktopServices, QFont, QImage, QKeySequence,
    QPainter, QPainterPath, QPen, QPixmap, QIcon,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog,
    QFrame, QGraphicsEllipseItem, QGraphicsItem, QGraphicsItemGroup,
    QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QSpinBox, QSplitter, QStackedWidget,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

RENDER_SCALE = 2.0

TOOLS = [
    ("select", "Seç", "🖱"),
    ("text", "Metin", "🅣"),
    ("ink", "Kalem", "✎"),
    ("line", "Çizgi / Ok", "↗"),
    ("rect", "Dörtgen", "▭"),
    ("oval", "Oval", "◯"),
    ("highlight", "Fosforlu", "🖍"),
    ("redact", "Gizle (Sansür)", "⬛"),
    ("number", "Numarala", "🔢"),
    ("crop", "Kırp / Yakala", "🖼"),
]

def number_to_color(n, saturation=200, value=225):
    """
    Her numaraya HEP AYNI ve birbirinden ayırt edilebilir bir renk üretir
    (altın açı / golden-angle hue dağılımı). Saf bir n->renk fonksiyonu
    olduğu için, iki farklı PDF'te bile aynı numara HER ZAMAN aynı rengi
    alır — Karşılaştırma modunda iki belge arasında numara eşleştirmesi
    yapmak için ekstra bir mekanizmaya gerek kalmaz. Eski sabit 8 renklik
    palette 9. numaradan itibaren tekrar ediyordu (1 ile 9 aynı renk); bu
    fonksiyon pratikte yüzlerce numaraya kadar çakışma olmadan çalışır.
    """
    hue = int((n * 137.508) % 360)   # altın açı: art arda gelen sayılar
                                     # birbirinden mümkün olduğunca uzak
                                     # tonlara düşer
    c = QColor()
    c.setHsv(hue, saturation, value)
    return c


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def qcolor_to_fitz(color: QColor):
    if color is None:
        return None
    return (color.redF(), color.greenF(), color.blueF())


def find_unicode_font():
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def arrow_wing_points(p1, p2, size=12.0, angle_deg=28.0):
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1e-6
    back = (-dx / length, -dy / length)
    ang = math.radians(angle_deg)

    def rot(vx, vy, theta):
        return (vx * math.cos(theta) - vy * math.sin(theta),
                vx * math.sin(theta) + vy * math.cos(theta))

    w1 = rot(*back, ang)
    w2 = rot(*back, -ang)
    return ((x2 + w1[0] * size, y2 + w1[1] * size),
            (x2 + w2[0] * size, y2 + w2[1] * size))


# ---------------------------------------------------------------------------
# Annotation model ailesi
# ---------------------------------------------------------------------------
class Annotation:
    def create_item(self) -> QGraphicsItem:
        raise NotImplementedError

    def apply_to_page(self, page, font_ctx):
        raise NotImplementedError

    def translate(self, dx, dy):
        raise NotImplementedError


class TextAnnotation(Annotation):
    def __init__(self, x, y, text, color: QColor, size: float):
        self.x, self.y = x, y
        self.text = text
        self.color = QColor(color)
        self.size = size

    def create_item(self):
        item = QGraphicsSimpleTextItem(self.text)
        font = QFont()
        font.setPixelSize(max(1, int(round(self.size))))
        item.setFont(font)
        item.setBrush(QBrush(self.color))
        item.setPos(self.x, self.y)
        return item

    def apply_to_page(self, page, font_ctx):
        color = qcolor_to_fitz(self.color)
        baseline = fitz.Point(self.x, self.y + self.size * 0.8)
        kwargs = dict(fontsize=self.size, color=color, fill_opacity=1)
        if font_ctx is not None:
            page.insert_text(baseline, self.text,
                             fontname=font_ctx[0], fontfile=font_ctx[1], **kwargs)
        else:
            page.insert_text(baseline, self.text, fontname="helv", **kwargs)

    def translate(self, dx, dy):
        self.x += dx
        self.y += dy


class InkAnnotation(Annotation):
    def __init__(self, points, color: QColor, width: float, opacity: float = 1.0):
        self.points = list(points)
        self.color = QColor(color)
        self.width = width
        self.opacity = opacity

    def create_item(self):
        path = QPainterPath()
        if self.points:
            path.moveTo(*self.points[0])
            for p in self.points[1:]:
                path.lineTo(*p)
            if len(self.points) == 1:
                path.lineTo(self.points[0][0] + 0.1, self.points[0][1])
        item = QGraphicsPathItem(path)
        pen = QPen(self.color, self.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        c = QColor(self.color)
        c.setAlphaF(self.opacity)
        pen.setColor(c)
        item.setPen(pen)
        return item

    def apply_to_page(self, page, font_ctx):
        color = qcolor_to_fitz(self.color)
        pts = [fitz.Point(x, y) for x, y in self.points]
        if len(pts) == 1:
            page.draw_circle(pts[0], self.width / 2, color=color, fill=color,
                             stroke_opacity=self.opacity, fill_opacity=self.opacity)
        else:
            page.draw_polyline(pts, color=color, width=self.width,
                               stroke_opacity=self.opacity, lineCap=1, lineJoin=1)

    def translate(self, dx, dy):
        self.points = [(x + dx, y + dy) for x, y in self.points]


class LineAnnotation(Annotation):
    def __init__(self, p1, p2, color: QColor, width: float,
                 opacity: float = 1.0, arrow: bool = True):
        self.p1, self.p2 = tuple(p1), tuple(p2)
        self.color = QColor(color)
        self.width = width
        self.opacity = opacity
        self.arrow = arrow

    def _segments(self):
        segs = [(self.p1, self.p2)]
        if self.arrow:
            w1, w2 = arrow_wing_points(self.p1, self.p2, size=max(10, self.width * 3.5))
            segs.append((self.p2, w1))
            segs.append((self.p2, w2))
        return segs

    def create_item(self):
        path = QPainterPath()
        for a, b in self._segments():
            path.moveTo(*a)
            path.lineTo(*b)
        item = QGraphicsPathItem(path)
        pen = QPen(self.color, self.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        c = QColor(self.color)
        c.setAlphaF(self.opacity)
        pen.setColor(c)
        item.setPen(pen)
        return item

    def apply_to_page(self, page, font_ctx):
        color = qcolor_to_fitz(self.color)
        for a, b in self._segments():
            page.draw_line(fitz.Point(*a), fitz.Point(*b), color=color,
                           width=self.width, stroke_opacity=self.opacity, lineCap=1)

    def translate(self, dx, dy):
        self.p1 = (self.p1[0] + dx, self.p1[1] + dy)
        self.p2 = (self.p2[0] + dx, self.p2[1] + dy)


class ShapeAnnotation(Annotation):
    def __init__(self, rect: QRectF, kind, fill: QColor, outline: QColor,
                 width: float, fill_opacity: float):
        self.rect = QRectF(rect).normalized()
        self.kind = kind
        self.fill = QColor(fill) if fill else None
        self.outline = QColor(outline) if outline else None
        self.width = width
        self.fill_opacity = fill_opacity

    def create_item(self):
        item = (QGraphicsEllipseItem(self.rect) if self.kind == "oval"
               else QGraphicsRectItem(self.rect))
        if self.fill is not None:
            fc = QColor(self.fill)
            fc.setAlphaF(self.fill_opacity)
            item.setBrush(QBrush(fc))
        else:
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        if self.outline is not None and self.width > 0:
            item.setPen(QPen(self.outline, self.width))
        else:
            item.setPen(QPen(Qt.PenStyle.NoPen))
        return item

    def apply_to_page(self, page, font_ctx):
        r = fitz.Rect(self.rect.left(), self.rect.top(),
                      self.rect.right(), self.rect.bottom())
        fill = qcolor_to_fitz(self.fill)
        outline = qcolor_to_fitz(self.outline)
        common = dict(color=outline, fill=fill,
                      width=self.width if outline else 0,
                      fill_opacity=self.fill_opacity, stroke_opacity=1)
        (page.draw_oval if self.kind == "oval" else page.draw_rect)(r, **common)

    def translate(self, dx, dy):
        self.rect.translate(dx, dy)


class HighlightAnnotation(Annotation):
    def __init__(self, rect: QRectF, color: QColor, opacity: float):
        self.rect = QRectF(rect).normalized()
        self.color = QColor(color)
        self.opacity = opacity

    def create_item(self):
        item = QGraphicsRectItem(self.rect)
        c = QColor(self.color)
        c.setAlphaF(self.opacity)
        item.setBrush(QBrush(c))
        item.setPen(QPen(Qt.PenStyle.NoPen))
        return item

    def apply_to_page(self, page, font_ctx):
        r = fitz.Rect(self.rect.left(), self.rect.top(),
                      self.rect.right(), self.rect.bottom())
        page.draw_rect(r, color=None, fill=qcolor_to_fitz(self.color),
                       fill_opacity=self.opacity)

    def translate(self, dx, dy):
        self.rect.translate(dx, dy)


class RedactionAnnotation(Annotation):
    """Gerçek sansürleme: dışa aktarırken altındaki içeriği kalıcı siler."""

    def __init__(self, rect: QRectF, fill_color: QColor):
        self.rect = QRectF(rect).normalized()
        self.fill_color = QColor(fill_color)

    def create_item(self):
        item = QGraphicsRectItem(self.rect)
        item.setBrush(QBrush(QColor(self.fill_color)))
        item.setPen(QPen(QColor("#ff4d4d"), 1.5, Qt.PenStyle.DashLine))
        return item

    def apply_to_page(self, page, font_ctx):
        r = fitz.Rect(self.rect.left(), self.rect.top(),
                      self.rect.right(), self.rect.bottom())
        page.add_redact_annot(r, fill=qcolor_to_fitz(self.fill_color) or (0, 0, 0))

    def translate(self, dx, dy):
        self.rect.translate(dx, dy)


class NumberAnnotation(Annotation):
    """Otomatik numaralandırma: renkli daire + içinde beyaz sayı."""

    def __init__(self, x, y, number: int, color: QColor, size: float):
        self.x, self.y = x, y
        self.number = number
        self.color = QColor(color)
        self.size = size

    def create_item(self):
        r = self.size * 0.9
        group = QGraphicsItemGroup()
        ellipse = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
        ellipse.setBrush(QBrush(QColor(self.color)))
        ellipse.setPen(QPen(Qt.PenStyle.NoPen))
        group.addToGroup(ellipse)

        text = QGraphicsSimpleTextItem(str(self.number))
        font = QFont()
        font.setPixelSize(max(1, int(round(self.size))))
        font.setBold(True)
        text.setFont(font)
        text.setBrush(QBrush(QColor("#ffffff")))
        tb = text.boundingRect()
        text.setPos(-tb.width() / 2, -tb.height() / 2)
        group.addToGroup(text)

        group.setPos(self.x, self.y)
        return group

    def apply_to_page(self, page, font_ctx):
        color = qcolor_to_fitz(self.color)
        r = self.size * 0.9
        page.draw_circle(fitz.Point(self.x, self.y), r, color=color, fill=color)
        text = str(self.number)
        tw = fitz.get_text_length(text, fontname="hebo", fontsize=self.size)
        baseline = fitz.Point(self.x - tw / 2, self.y + self.size * 0.35)
        page.insert_text(baseline, text, fontsize=self.size, color=(1, 1, 1),
                         fontname="hebo")

    def translate(self, dx, dy):
        self.x += dx
        self.y += dy


# ---------------------------------------------------------------------------
# PDF belgesi sarmalayıcı
# ---------------------------------------------------------------------------
class PDFDocument:
    """PyMuPDF belgesini sarar: render, metin çıkarımı, dışa aktarım ve
    sayfa yönetimi (ekle/sil/kopyala/taşı)."""

    def __init__(self, path):
        self.path = path
        self.doc = fitz.open(path)
        self._pix_cache = {}
        self._lines_cache = {}

    @property
    def page_count(self):
        return self.doc.page_count

    def page_size(self, index):
        r = self.doc[index].rect
        return r.width, r.height

    def _invalidate(self):
        """Sayfa yapısı değiştiğinde (ekle/sil/taşı) tüm önbellekleri
        temizler; sayfa indeksleri kaymış olabileceğinden kısmi
        güncelleme riskli olur."""
        self._pix_cache.clear()
        self._lines_cache.clear()

    def render_pixmap(self, index) -> QPixmap:
        if index not in self._pix_cache:
            page = self.doc[index]
            pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE),
                                  alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format.Format_RGB888).copy()
            self._pix_cache[index] = QPixmap.fromImage(img)
        return self._pix_cache[index]

    def text_lines(self, index):
        if index not in self._lines_cache:
            page = self.doc[index]
            rects = []
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    rects.append(fitz.Rect(line["bbox"]))
            self._lines_cache[index] = rects
        return self._lines_cache[index]

    def thumbnail(self, index, scale=0.2) -> QPixmap:
        page = self.doc[index]
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(img)

    # -- sayfa yönetimi ---------------------------------------------------
    def delete_page(self, index):
        self.doc.delete_page(index)
        self._invalidate()

    def insert_blank_page(self, index, width, height):
        self.doc.new_page(pno=index, width=width, height=height)
        self._invalidate()

    def duplicate_page(self, index):
        """index'teki sayfanın kopyasını hemen sonrasına ekler."""
        self.doc.copy_page(index, to=index)
        self._invalidate()

    def move_page(self, frm, to):
        self.doc.move_page(frm, to)
        self._invalidate()

    # -- dışa aktarım -------------------------------------------------------
    def _annotated_copy(self, annotations_by_page, font_ctx):
        tmp = fitz.open("pdf", self.doc.write())
        for pidx, anns in annotations_by_page.items():
            if not anns:
                continue
            page = tmp[pidx]
            redactions = [a for a in anns if isinstance(a, RedactionAnnotation)]
            others = [a for a in anns if not isinstance(a, RedactionAnnotation)]
            for r in redactions:
                r.apply_to_page(page, font_ctx)
            if redactions:
                page.apply_redactions()
            for a in others:
                a.apply_to_page(page, font_ctx)
        return tmp

    def export_pdf(self, out_path, annotations_by_page, font_ctx):
        tmp = self._annotated_copy(annotations_by_page, font_ctx)
        tmp.save(out_path, garbage=3, deflate=True)
        tmp.close()

    def export_image(self, index, page_annotations, font_ctx, out_path, zoom=3.0):
        tmp = self._annotated_copy({index: page_annotations}, font_ctx)
        pix = tmp[index].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(out_path)
        tmp.close()


# ---------------------------------------------------------------------------
# Araç durumu
# ---------------------------------------------------------------------------
class ToolState:
    def __init__(self):
        self.tool = "select"
        self.stroke_color = QColor("#e63946")
        self.fill_color = QColor("#ffd166")
        self.text_color = QColor("#1d3557")
        self.hl_color = QColor("#fff176")
        self.redact_color = QColor("#000000")
        self.number_color = QColor("#e63946")
        self.pen_width = 3.0
        self.pen_opacity = 1.0
        self.font_size = 20.0
        self.hl_thickness = 14.0
        self.fill_opacity = 0.40
        self.snap = True
        self.line_arrow = True
        self.number_start = 1
        self.number_size = 18.0
        self.number_multicolor = False
        # Karşılaştırmada "Eşleştir modu": açıkken bir panele numara koyup
        # sonra diğer panele koyunca İKİSİ DE aynı numarayı/rengi alır, sonra
        # sayaç bir artar. Böylece iki belge arasında birebir eşleşen çiftler
        # oluşturulur.
        self.number_pair_mode = False


# ---------------------------------------------------------------------------
# Belge oturumu — açık bir PDF'in tüm durumu
# ---------------------------------------------------------------------------
class DocumentSession:
    def __init__(self, pdf: PDFDocument, path: str):
        self.pdf = pdf
        self.path = path
        self.title = os.path.basename(path)
        self.annotations = {i: [] for i in range(pdf.page_count)}
        self.current_page = 0
        self.history = HistoryManager(self)
        self.number_counter = None   # None = henüz başlamadı


# ---------------------------------------------------------------------------
# Geri Al / Yinele — tek bir session'a bağlı
# ---------------------------------------------------------------------------
class HistoryManager:
    """Bir DocumentSession'ın annotations sözlüğü üzerinde çalışır.

    Numara ekleme işlemlerinde, geri alındığında/yinelendiğinde ilgili sayacı
    da düzeltmek için isteğe bağlı bir 'counter_undo'/'counter_redo' geri
    çağrısı saklanabilir. Böylece bir numarayı geri alınca sayaç da bir azalır
    (yoksa 1 koy → geri al → tekrar koy = 2 gibi kafa karıştırıcı bir davranış
    oluşuyordu)."""

    def __init__(self, session):
        self.session = session
        self.undo_stack = []
        self.redo_stack = []

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()

    def push_add(self, page, ann, on_undo=None, on_redo=None):
        self.undo_stack.append(("add", page, ann, on_undo, on_redo))
        self.redo_stack.clear()

    def push_remove(self, page, ann):
        self.undo_stack.append(("remove", page, ann, None, None))
        self.redo_stack.clear()

    def push_move(self, page, ann, dx, dy):
        self.undo_stack.append(("move", page, ann, dx, dy))
        self.redo_stack.clear()

    def _apply(self, entry, forward):
        action, page, ann = entry[0], entry[1], entry[2]
        anns = self.session.annotations.setdefault(page, [])
        if action == "add":
            if forward:
                if ann not in anns:
                    anns.append(ann)
                cb = entry[4]   # on_redo
            else:
                if ann in anns:
                    anns.remove(ann)
                cb = entry[3]   # on_undo
            if callable(cb):
                cb()
        elif action == "remove":
            if forward:
                if ann in anns:
                    anns.remove(ann)
            else:
                if ann not in anns:
                    anns.append(ann)
        elif action == "move":
            dx, dy = entry[3], entry[4]
            ann.translate(dx, dy) if forward else ann.translate(-dx, -dy)
        return page

    def undo(self):
        if not self.undo_stack:
            return None
        entry = self.undo_stack.pop()
        page = self._apply(entry, forward=False)
        self.redo_stack.append(entry)
        return page

    def redo(self):
        if not self.redo_stack:
            return None
        entry = self.redo_stack.pop()
        page = self._apply(entry, forward=True)
        self.undo_stack.append(entry)
        return page


# ---------------------------------------------------------------------------
# Çizim yüzeyi
# ---------------------------------------------------------------------------
class PDFView(QGraphicsView):
    annotationCreated = Signal(object)
    textRequested = Signal(QPointF)
    numberRequested = Signal(QPointF)
    cropRequested = Signal(QRectF)
    itemsMoved = Signal()
    contextMenuRequested = Signal(object, object)

    def __init__(self, state: ToolState):
        super().__init__()
        self.state = state
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#2b2d31"))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._panning = False
        self._pan_last = QPointF()
        self._drawing = False
        self._start = QPointF()
        self._temp_item = None
        self._ink_points = []
        self._hl_fixed_y = None
        self._line_end = QPointF()
        self.page_lines = []

    def _snap_band(self, scene_pt, force_free):
        y = scene_pt.y()
        if self.state.snap and not force_free and self.page_lines:
            best, best_d = None, 6.0
            for r in self.page_lines:
                if r.y0 - best_d <= y <= r.y1 + best_d:
                    d = abs((r.y0 + r.y1) / 2 - y)
                    if d < best_d or best is None:
                        best, best_d = r, d
            if best is not None:
                return best.y0, best.y1
        half = self.state.hl_thickness / 2
        return y - half, y + half

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        self.contextMenuRequested.emit(item, self.mapToGlobal(pos))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_last = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        pt = self.mapToScene(e.position().toPoint())
        tool = self.state.tool

        if tool == "select":
            super().mousePressEvent(e)
            return

        if tool == "text":
            if e.button() == Qt.MouseButton.LeftButton:
                self.textRequested.emit(pt)
            return

        if tool == "number":
            if e.button() == Qt.MouseButton.LeftButton:
                self.numberRequested.emit(pt)
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        self._drawing = True
        self._start = pt

        if tool == "ink":
            self._ink_points = [(pt.x(), pt.y())]
            path = QPainterPath()
            path.moveTo(pt)
            self._temp_item = QGraphicsPathItem(path)
            pen = QPen(self.state.stroke_color, self.state.pen_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            c = QColor(self.state.stroke_color)
            c.setAlphaF(self.state.pen_opacity)
            pen.setColor(c)
            self._temp_item.setPen(pen)
            self.scene_obj.addItem(self._temp_item)

        elif tool == "line":
            self._line_end = pt
            self._temp_item = QGraphicsPathItem(QPainterPath())
            pen = QPen(self.state.stroke_color, self.state.pen_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            c = QColor(self.state.stroke_color)
            c.setAlphaF(self.state.pen_opacity)
            pen.setColor(c)
            self._temp_item.setPen(pen)
            self.scene_obj.addItem(self._temp_item)

        elif tool in ("rect", "oval"):
            self._temp_item = (QGraphicsEllipseItem() if tool == "oval"
                               else QGraphicsRectItem())
            fc = QColor(self.state.fill_color)
            fc.setAlphaF(self.state.fill_opacity)
            self._temp_item.setBrush(QBrush(fc))
            self._temp_item.setPen(QPen(self.state.stroke_color, self.state.pen_width))
            self.scene_obj.addItem(self._temp_item)

        elif tool == "redact":
            self._temp_item = QGraphicsRectItem()
            self._temp_item.setBrush(QBrush(QColor(self.state.redact_color)))
            self._temp_item.setPen(QPen(QColor("#ff4d4d"), 1.5, Qt.PenStyle.DashLine))
            self.scene_obj.addItem(self._temp_item)

        elif tool == "crop":
            # PDF'i DEĞİŞTİRMEZ; sadece seçilen alanı görsel olarak çıkarır.
            # Bu yüzden diğer araçlardan ayırt edilsin diye mavi kesikli
            # çerçeve + hafif dolgu kullanılıyor (kırmızı = sansür ile
            # karışmasın).
            self._temp_item = QGraphicsRectItem()
            fc = QColor("#3a86ff"); fc.setAlphaF(0.12)
            self._temp_item.setBrush(QBrush(fc))
            self._temp_item.setPen(QPen(QColor("#3a86ff"), 1.5, Qt.PenStyle.DashLine))
            self.scene_obj.addItem(self._temp_item)

        elif tool == "highlight":
            force_free = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._hl_fixed_y = self._snap_band(pt, force_free)
            self._temp_item = QGraphicsRectItem()
            c = QColor(self.state.hl_color)
            c.setAlphaF(self.state.fill_opacity)
            self._temp_item.setBrush(QBrush(c))
            self._temp_item.setPen(QPen(Qt.PenStyle.NoPen))
            self.scene_obj.addItem(self._temp_item)

    def mouseMoveEvent(self, e):
        if self._panning:
            delta = e.position() - self._pan_last
            self._pan_last = e.position()
            self.horizontalScrollBar().setValue(
                int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(
                int(self.verticalScrollBar().value() - delta.y()))
            return

        if not self._drawing:
            super().mouseMoveEvent(e)
            return

        pt = self.mapToScene(e.position().toPoint())
        tool = self.state.tool

        if tool == "ink":
            self._ink_points.append((pt.x(), pt.y()))
            path = self._temp_item.path()
            path.lineTo(pt)
            self._temp_item.setPath(path)

        elif tool == "line":
            end = pt
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                dx, dy = pt.x() - self._start.x(), pt.y() - self._start.y()
                dist = math.hypot(dx, dy) or 1.0
                angle = math.atan2(dy, dx)
                step = math.radians(45)
                snapped = round(angle / step) * step
                end = QPointF(self._start.x() + dist * math.cos(snapped),
                              self._start.y() + dist * math.sin(snapped))
            self._line_end = end
            path = QPainterPath()
            path.moveTo(self._start)
            path.lineTo(end)
            if self.state.line_arrow:
                w1, w2 = arrow_wing_points((self._start.x(), self._start.y()),
                                           (end.x(), end.y()),
                                           size=max(10, self.state.pen_width * 3.5))
                path.moveTo(end); path.lineTo(*w1)
                path.moveTo(end); path.lineTo(*w2)
            self._temp_item.setPath(path)

        elif tool in ("rect", "oval", "redact", "crop"):
            self._temp_item.setRect(QRectF(self._start, pt).normalized())

        elif tool == "highlight":
            y0, y1 = self._hl_fixed_y
            x0, x1 = sorted((self._start.x(), pt.x()))
            self._temp_item.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if not self._drawing:
            super().mouseReleaseEvent(e)
            if self.state.tool == "select":
                self.itemsMoved.emit()
            return

        self._drawing = False
        pt = self.mapToScene(e.position().toPoint())
        tool = self.state.tool
        ann = None

        if tool == "ink":
            if len(self._ink_points) >= 1:
                ann = InkAnnotation(self._ink_points, self.state.stroke_color,
                                    self.state.pen_width, self.state.pen_opacity)
        elif tool == "line":
            end = self._line_end
            if math.hypot(end.x() - self._start.x(), end.y() - self._start.y()) > 2:
                ann = LineAnnotation((self._start.x(), self._start.y()),
                                     (end.x(), end.y()), self.state.stroke_color,
                                     self.state.pen_width, self.state.pen_opacity,
                                     self.state.line_arrow)
        elif tool in ("rect", "oval"):
            rect = QRectF(self._start, pt).normalized()
            if rect.width() > 2 and rect.height() > 2:
                ann = ShapeAnnotation(rect, tool, self.state.fill_color,
                                      self.state.stroke_color, self.state.pen_width,
                                      self.state.fill_opacity)
        elif tool == "redact":
            rect = QRectF(self._start, pt).normalized()
            if rect.width() > 2 and rect.height() > 2:
                ann = RedactionAnnotation(rect, self.state.redact_color)
        elif tool == "crop":
            rect = QRectF(self._start, pt).normalized()
            if self._temp_item is not None:
                self.scene_obj.removeItem(self._temp_item)
                self._temp_item = None
            if rect.width() > 2 and rect.height() > 2:
                self.cropRequested.emit(rect)
            self._ink_points = []
            self._hl_fixed_y = None
            return   # annotation oluşturulmaz, PDF değişmez
        elif tool == "highlight":
            y0, y1 = self._hl_fixed_y
            x0, x1 = sorted((self._start.x(), pt.x()))
            if x1 - x0 > 2:
                rect = QRectF(x0, y0, x1 - x0, y1 - y0)
                ann = HighlightAnnotation(rect, self.state.hl_color, self.state.fill_opacity)

        if self._temp_item is not None:
            self.scene_obj.removeItem(self._temp_item)
            self._temp_item = None
        self._ink_points = []
        self._hl_fixed_y = None

        if ann is not None:
            self.annotationCreated.emit(ann)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
        else:
            super().wheelEvent(e)


# ---------------------------------------------------------------------------
# Karşılaştırma modu — bağımsız belge/sayfa seçimli panel. Çizim araçlarına
# kapalıdır (iki farklı belge/sayfa aynı anda gösterildiği için genel çizim
# anlamsızlaşır) ama NUMARALANDIRMA özel olarak desteklenir: kullanıcı iki
# belge arasında karşılık gelen noktaları işaretleyebilsin diye.
# ---------------------------------------------------------------------------
class ComparePaneView(QGraphicsView):
    """Karşılaştırma panelinin görüntüleyicisi.
    - 'Numarala' aracı aktifken sol tık numara koyar.
    - Fare tekerleği DOĞRUDAN (Ctrl'siz) yakınlaştırır — imleç hangi panelin
      üstündeyse yalnızca o panel yakınlaşır, çünkü olay o panele aittir.
    - Orta tuşla sürükleyerek kaydırma (pan)."""
    numberClicked = Signal(QPointF)

    def __init__(self, scene, state_provider):
        super().__init__(scene)
        self.state_provider = state_provider
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._panning = False
        self._pan_last = QPointF()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_last = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        state = self.state_provider()
        if state is not None and state.tool == "number" and e.button() == Qt.MouseButton.LeftButton:
            pt = self.mapToScene(e.position().toPoint())
            self.numberClicked.emit(pt)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning:
            delta = e.position() - self._pan_last
            self._pan_last = e.position()
            self.horizontalScrollBar().setValue(
                int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(
                int(self.verticalScrollBar().value() - delta.y()))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        # Ana görünümden farklı olarak burada Ctrl GEREKMEZ: karşılaştırmada
        # asıl istenen davranış, imlecin üstünde olduğu paneli tek başına
        # yakınlaştırmak. Olay yalnızca imlecin üstündeki view'a geldiği için
        # diğer panel etkilenmez.
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class ComparePane(QWidget):
    def __init__(self, sessions_provider, state_provider, number_callback):
        super().__init__()
        self.sessions_provider = sessions_provider
        self.session = None
        self.page = 0

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_combo_change)
        v.addWidget(self.combo)

        self.scene = QGraphicsScene()
        self.gview = ComparePaneView(self.scene, state_provider)
        self.gview.setRenderHints(QPainter.RenderHint.Antialiasing |
                                  QPainter.RenderHint.SmoothPixmapTransform)
        self.gview.numberClicked.connect(lambda pt: number_callback(self, pt))
        v.addWidget(self.gview, 1)

        nav = QWidget()
        h = QHBoxLayout(nav)
        h.setContentsMargins(0, 0, 0, 0)
        self.btn_prev = QPushButton("◀")
        self.btn_prev.clicked.connect(lambda: self._change_page(-1))
        self.lbl = QLabel("0 / 0")
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_next = QPushButton("▶")
        self.btn_next.clicked.connect(lambda: self._change_page(1))
        self.btn_fit = QPushButton("⤢")
        self.btn_fit.setToolTip("Sayfaya sığdır (yakınlaştırmayı sıfırla)")
        self.btn_fit.clicked.connect(lambda: self._render(fit=True))
        h.addWidget(self.btn_prev)
        h.addWidget(self.lbl, 1)
        h.addWidget(self.btn_next)
        h.addWidget(self.btn_fit)
        v.addWidget(nav)

    def refresh_sessions(self):
        prev_title = self.combo.currentText()
        sessions = self.sessions_provider()
        self.combo.blockSignals(True)
        self.combo.clear()
        for s in sessions:
            self.combo.addItem(s.title)
        idx = self.combo.findText(prev_title)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        elif sessions:
            self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)
        self._on_combo_change(self.combo.currentIndex())

    def _on_combo_change(self, idx):
        sessions = self.sessions_provider()
        if 0 <= idx < len(sessions):
            self.session = sessions[idx]
            self.page = min(self.page, max(0, self.session.pdf.page_count - 1))
        else:
            self.session = None
        self._render(fit=True)

    def _change_page(self, delta):
        if not self.session:
            return
        self.page = max(0, min(self.page + delta, self.session.pdf.page_count - 1))
        self._render(fit=True)

    def _render(self, fit=False):
        # fit=True yalnızca belge/sayfa değiştiğinde çağrılır; numara ekleme
        # gibi güncellemelerde fit yapılmaz ki kullanıcının tekerlekle
        # ayarladığı yakınlaştırma sıfırlanmasın.
        current_transform = self.gview.transform()
        self.scene.clear()
        if not self.session:
            self.lbl.setText("0 / 0")
            return
        pix = self.session.pdf.render_pixmap(self.page)
        item = self.scene.addPixmap(pix)
        item.setScale(1.0 / RENDER_SCALE)
        w, h = self.session.pdf.page_size(self.page)
        self.scene.setSceneRect(0, 0, w, h)
        for ann in self.session.annotations.get(self.page, []):
            gi = ann.create_item()
            gi.setFlags(QGraphicsItem.GraphicsItemFlag(0))   # salt okunur önizleme
            self.scene.addItem(gi)
        self.lbl.setText(f"{self.page + 1} / {self.session.pdf.page_count}")
        if fit:
            self.gview.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.gview.setTransform(current_transform)


# ---------------------------------------------------------------------------
# Ana pencere
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF İşaretleme Aracı")
        self.resize(1440, 900)
        self.setMinimumWidth(560)

        self.state = ToolState()
        self.sessions = []            # açık DocumentSession listesi
        self.active_session = None
        self.item_ann_map = {}
        self.font_ctx = self._resolve_font()
        self._color_buttons_by_attr = {}
        self._tool_buttons = []
        self._tool_pages = {}
        # Karşılaştırma "Eşleştir modu" durumu
        self._pair_counter = 1
        self._pair_pending = None   # None ya da (pane, numara, renk)
        # Global işlem sırası: her işlemin HANGİ session'da yapıldığını izler,
        # böylece Ctrl+Z karşılaştırma modunda başka bir belgeye eklenen
        # numarayı da doğru belgede geri alır (yalnızca aktif belgeye bakmaz).
        self._global_undo = []   # [session, ...]
        self._global_redo = []

        self._build_ui()
        self._apply_style()
        self._set_ui_enabled(False)
        self._select_tool("select", self.btn_select)

    def _resolve_font(self):
        path = find_unicode_font()
        return ("trfont", path) if path else None

    # =====================================================================
    # UI kurulumu
    # =====================================================================
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = self._build_toolbar()
        tb_scroll = QScrollArea()
        tb_scroll.setWidget(toolbar)
        tb_scroll.setWidgetResizable(True)
        tb_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tb_scroll.setFixedHeight(toolbar.sizeHint().height() + 14)
        root.addWidget(tb_scroll)

        body = QHBoxLayout()
        body.setContentsMargins(8, 8, 8, 8)
        body.setSpacing(8)

        body.addWidget(self._build_left_column())
        body.addWidget(self._build_tool_column())
        body.addWidget(self._build_canvas_stack(), 1)
        body.addWidget(self._build_side_panel())

        body_wrap = QWidget()
        body_wrap.setLayout(body)
        root.addWidget(body_wrap, 1)

        self.status = QLabel("PDF açmak için 'Aç' düğmesine tıklayın.")
        self.status.setObjectName("status")
        self.status.setContentsMargins(12, 6, 12, 6)
        root.addWidget(self.status)

        self.setCentralWidget(central)
        self._build_shortcuts()

    # -- ortak küçük yapı taşları ------------------------------------------
    def _sep(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setObjectName("sep")
        return line

    def _framed(self, widget, title):
        frame = QFrame()
        frame.setObjectName("sectionFrame")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        cap = QLabel(title)
        cap.setObjectName("sectionTitle")
        lay.addWidget(cap)
        lay.addWidget(widget, 1)
        return frame

    def _toolbar_group(self, title, widgets):
        frame = QFrame()
        frame.setObjectName("tbGroup")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(8, 4, 8, 6)
        outer.setSpacing(3)
        cap = QLabel(title)
        cap.setObjectName("tbGroupTitle")
        outer.addWidget(cap)
        row = QHBoxLayout()
        row.setSpacing(6)
        for w in widgets:
            row.addWidget(w)
        outer.addLayout(row)
        return frame

    def _slider_row(self, label, lo, hi, val, setter, suffix=""):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setFixedWidth(78)
        h.addWidget(lbl)
        s = QSlider(Qt.Orientation.Horizontal)
        s.setMinimum(lo); s.setMaximum(hi); s.setValue(val)
        h.addWidget(s, 1)
        val_lbl = QLabel(f"{val}{suffix}")
        val_lbl.setFixedWidth(34)
        h.addWidget(val_lbl)

        def on_change(v):
            setter(v)
            val_lbl.setText(f"{v}{suffix}")

        s.valueChanged.connect(on_change)
        return row

    def _spin_row(self, label, spin):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setFixedWidth(78)
        h.addWidget(lbl)
        h.addWidget(spin, 1)
        return row

    def _color_row(self, label, attr):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setFixedWidth(78)
        h.addWidget(lbl)
        btn = QPushButton()
        btn.setObjectName("colorbtn")
        btn.setFixedHeight(26)
        color = getattr(self.state, attr)
        btn.setStyleSheet(f"background:{color.name()};")
        btn.clicked.connect(lambda: self._pick_color(attr))
        h.addWidget(btn, 1)
        self._color_buttons_by_attr.setdefault(attr, []).append(btn)
        return row

    def _checkbox_row(self, label, attr, tooltip=""):
        chk = QCheckBox(label)
        chk.setChecked(bool(getattr(self.state, attr)))
        if tooltip:
            chk.setToolTip(tooltip)
        chk.toggled.connect(lambda v: setattr(self.state, attr, v))
        return chk

    def _hint(self, text):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setObjectName("hint")
        return lbl

    # -- üst çubuk ----------------------------------------------------------
    def _build_toolbar(self):
        bar = QWidget()
        bar.setObjectName("toolbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)

        self.btn_open = QPushButton("📂 Aç")
        self.btn_open.setObjectName("primary")
        self.btn_open.clicked.connect(self.open_pdf)

        self.btn_merge = QPushButton("🔗 Birleştir")
        self.btn_merge.clicked.connect(self.merge_pdfs)

        self.btn_split = QToolButton()
        self.btn_split.setText("✂ Ayır")
        self.btn_split.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        split_menu = QMenu(self.btn_split)
        split_menu.addAction("🗂 Her sayfayı ayrı PDF yap (klasöre)", self.split_all_pages)
        split_menu.addAction("✂ Sayfa aralığını yeni PDF olarak çıkar…", self.extract_page_range)
        self.btn_split.setMenu(split_menu)

        self.btn_export = QToolButton()
        self.btn_export.setText("⇩ Dışa Aktar")
        self.btn_export.setObjectName("primary")
        self.btn_export.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self.btn_export)
        export_menu.addAction("📄 PDF olarak kaydet", self.save_pdf)
        export_menu.addAction("🖼 Bu sayfayı PNG", lambda: self.export_image_current("png"))
        export_menu.addAction("🖼 Bu sayfayı JPEG", lambda: self.export_image_current("jpg"))
        export_menu.addSeparator()
        export_menu.addAction("🗂 Tüm sayfaları PNG (klasöre)",
                              lambda: self.export_all_pages_images("png"))
        self.btn_export.setMenu(export_menu)

        lay.addWidget(self._toolbar_group(
            "DOSYA", [self.btn_open, self.btn_merge, self.btn_split, self.btn_export]))

        self.btn_undo = QPushButton("↶ Geri")
        self.btn_undo.clicked.connect(self.undo_active)
        self.btn_redo = QPushButton("↷ Yinele")
        self.btn_redo.clicked.connect(self.redo_active)
        lay.addWidget(self._toolbar_group("DÜZENLE", [self.btn_undo, self.btn_redo]))

        self.btn_prev = QPushButton("◀")
        self.btn_prev.clicked.connect(lambda: self.goto_page(self.active_session.current_page - 1)
                                      if self.active_session else None)
        self.lbl_page = QLabel("0 / 0")
        self.lbl_page.setObjectName("pagelbl")
        self.btn_next = QPushButton("▶")
        self.btn_next.clicked.connect(lambda: self.goto_page(self.active_session.current_page + 1)
                                      if self.active_session else None)
        self.btn_fit = QPushButton("⤢ Sığdır")
        self.btn_fit.clicked.connect(self.fit_width)
        self.btn_add_page = QPushButton("+ Sayfa")
        self.btn_add_page.setToolTip("Aktif sayfadan sonra boş sayfa ekler")
        self.btn_add_page.clicked.connect(
            lambda: self._insert_blank_page(self.active_session.current_page + 1)
            if self.active_session else None)
        lay.addWidget(self._toolbar_group(
            "SAYFA", [self.btn_prev, self.lbl_page, self.btn_next, self.btn_fit, self.btn_add_page]))

        self.btn_theme = QPushButton("☀ Açık Tema")
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.btn_compare = QPushButton("⚏ Karşılaştır")
        self.btn_compare.setCheckable(True)
        self.btn_compare.toggled.connect(self.toggle_compare_mode)
        lay.addWidget(self._toolbar_group("GÖRÜNÜM", [self.btn_theme, self.btn_compare]))

        lay.addStretch(1)
        return bar

    # -- sol: belgeler + sayfalar --------------------------------------------
    def _build_left_column(self):
        self.doc_list = QListWidget()
        self.doc_list.setObjectName("doclist")
        self.doc_list.currentRowChanged.connect(self._on_doclist_selected)
        self.doc_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.doc_list.customContextMenuRequested.connect(self._on_doclist_context_menu)
        doc_frame = self._framed(self.doc_list, "BELGELER (sağ tık: klasör/ad/kapat)")
        doc_frame.setFixedHeight(180)

        self.thumb_list = QListWidget()
        self.thumb_list.setIconSize(QSize(120, 156))
        self.thumb_list.setSpacing(8)
        self.thumb_list.currentRowChanged.connect(self._on_thumb_selected)
        self.thumb_list.setObjectName("thumbs")
        self.thumb_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thumb_list.customContextMenuRequested.connect(self._on_thumb_context_menu)
        thumb_frame = self._framed(self.thumb_list, "SAYFALAR (sağ tık: ekle/sil/taşı)")

        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(doc_frame)
        v.addWidget(thumb_frame, 1)
        col.setFixedWidth(210)
        return col

    # -- sol: dikey araç sütunu ----------------------------------------------
    def _build_tool_column(self):
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        for key, label, icon in TOOLS:
            btn = QToolButton()
            btn.setText(f"{icon}\n{label}")
            btn.setCheckable(True)
            btn.setObjectName("toolbtn")
            btn.setMinimumHeight(52)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _, k=key, b=btn: self._select_tool(k, b))
            self._tool_buttons.append((key, btn))
            v.addWidget(btn)
        v.addStretch(1)
        frame = self._framed(col, "ARAÇLAR")
        frame.setFixedWidth(96)
        self.btn_select = dict(self._tool_buttons)["select"]
        return frame

    # -- orta: normal görünüm / karşılaştırma ---------------------------------
    def _build_canvas_stack(self):
        self.view = PDFView(self.state)
        self.view.annotationCreated.connect(self.add_annotation)
        self.view.textRequested.connect(self.on_text_requested)
        self.view.numberRequested.connect(self.on_number_requested)
        self.view.cropRequested.connect(self.on_crop_requested)
        self.view.itemsMoved.connect(self.sync_moved_items)
        self.view.contextMenuRequested.connect(self.show_item_context_menu)
        normal_frame = self._framed(self.view, "SAYFA")

        self.pane_left = ComparePane(lambda: self.sessions, lambda: self.state,
                                     self.on_number_requested_in_pane)
        self.pane_right = ComparePane(lambda: self.sessions, lambda: self.state,
                                      self.on_number_requested_in_pane)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.pane_left)
        splitter.addWidget(self.pane_right)
        compare_frame = self._framed(splitter, "KARŞILAŞTIRMA (salt okunur)")

        self.canvas_stack = QStackedWidget()
        self.canvas_stack.addWidget(normal_frame)     # index 0
        self.canvas_stack.addWidget(compare_frame)    # index 1
        return self.canvas_stack

    # -- sağ: özellikler + katmanlar -------------------------------------------
    def _build_side_panel(self):
        tabs = QTabWidget()
        tabs.setFixedWidth(230)
        tabs.addTab(self._build_properties_stack(), "Özellikler")
        tabs.addTab(self._build_layers_tab(), "Katmanlar")
        self.side_tabs = tabs
        return tabs

    def _build_properties_stack(self):
        self.prop_stack = QStackedWidget()

        def page(*rows):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(10, 10, 10, 10)
            v.setSpacing(10)
            for r in rows:
                v.addWidget(r)
            v.addStretch(1)
            return w

        idx = 0

        self.prop_stack.addWidget(page(
            self._hint("Ögeyi sürükleyerek taşıyabilirsiniz.\n"
                      "Silmek için Del tuşuna basın ya da\n"
                      "üzerinde sağ tıklayın."),
        ))
        self._tool_pages["select"] = idx; idx += 1

        self.prop_stack.addWidget(page(
            self._color_row("Renk", "text_color"),
            self._slider_row("Punto", 8, 96, int(self.state.font_size),
                             lambda v: setattr(self.state, "font_size", float(v))),
            self._hint("Sayfada bir noktaya tıklayıp metni yazın."),
        ))
        self._tool_pages["text"] = idx; idx += 1

        self.prop_stack.addWidget(page(
            self._color_row("Renk", "stroke_color"),
            self._slider_row("Kalınlık", 1, 30, int(self.state.pen_width),
                             lambda v: setattr(self.state, "pen_width", float(v))),
            self._slider_row("Opaklık", 10, 100, int(self.state.pen_opacity * 100),
                             lambda v: setattr(self.state, "pen_opacity", v / 100.0), "%"),
        ))
        self._tool_pages["ink"] = idx; idx += 1

        self.prop_stack.addWidget(page(
            self._color_row("Renk", "stroke_color"),
            self._slider_row("Kalınlık", 1, 20, int(self.state.pen_width),
                             lambda v: setattr(self.state, "pen_width", float(v))),
            self._slider_row("Opaklık", 10, 100, int(self.state.pen_opacity * 100),
                             lambda v: setattr(self.state, "pen_opacity", v / 100.0), "%"),
            self._checkbox_row("Ok ucu", "line_arrow"),
            self._hint("Shift: 45° açı kilidi."),
        ))
        self._tool_pages["line"] = idx; idx += 1

        shape_page = page(
            self._color_row("Dolgu", "fill_color"),
            self._color_row("Kenar", "stroke_color"),
            self._slider_row("Kenar Kal.", 0, 15, int(self.state.pen_width),
                             lambda v: setattr(self.state, "pen_width", float(v))),
            self._slider_row("Dolgu Opk.", 5, 100, int(self.state.fill_opacity * 100),
                             lambda v: setattr(self.state, "fill_opacity", v / 100.0), "%"),
            self._hint("⚠ Sadece görsel bir katmandır; altındaki\n"
                      "metin silinmez. Kalıcı gizleme için\n"
                      "'Gizle (Sansür)' aracını kullanın."),
        )
        self.prop_stack.addWidget(shape_page)
        self._tool_pages["rect"] = idx
        self._tool_pages["oval"] = idx
        idx += 1

        self.prop_stack.addWidget(page(
            self._color_row("Renk", "hl_color"),
            self._slider_row("Bant Kal.", 4, 40, int(self.state.hl_thickness),
                             lambda v: setattr(self.state, "hl_thickness", float(v))),
            self._slider_row("Opaklık", 10, 100, int(self.state.fill_opacity * 100),
                             lambda v: setattr(self.state, "fill_opacity", v / 100.0), "%"),
            self._checkbox_row("Satıra yapış", "snap"),
            self._hint("Satır yoksa bant Bant-Kalınlığı ile\n"
                      "yatay kilitlenir. Shift: yapışmayı\n"
                      "geçici kapatır.\n\n⚠ Bu da sadece görseldir."),
        ))
        self._tool_pages["highlight"] = idx; idx += 1

        self.prop_stack.addWidget(page(
            self._color_row("Kutu Rengi", "redact_color"),
            self._hint("🔒 Dışa aktarırken kutunun altındaki\n"
                      "metin/görsel PDF'ten KALICI olarak\n"
                      "silinir. Kırmızı kesikli çerçeve ile\n"
                      "gösterilir. Dikkatli çizin."),
        ))
        self._tool_pages["redact"] = idx; idx += 1

        start_spin = QSpinBox()
        start_spin.setRange(1, 9999)
        start_spin.setValue(int(self.state.number_start))
        start_spin.valueChanged.connect(self._on_number_start_changed)
        self.lbl_next_number = QLabel("Sıradaki: 1")
        self.lbl_next_number.setObjectName("nextNumber")
        reset_btn = QPushButton("🔁 Sayacı Sıfırla")
        reset_btn.clicked.connect(self.reset_number_counter)

        self.chk_pair = QCheckBox("Eşleştir modu (karşılaştırmada)")
        self.chk_pair.setToolTip("Açıkken: bir panele koyduğun numarayı diğer "
                                 "panele de aynı numara/renkle koyabilirsin.")
        self.chk_pair.toggled.connect(self._on_pair_toggled)
        reset_pair_btn = QPushButton("🔁 Eşleştirme Sayacını Sıfırla")
        reset_pair_btn.clicked.connect(self.reset_pair_counter)

        self.prop_stack.addWidget(page(
            self._spin_row("Başlangıç", start_spin),
            self._slider_row("Punto", 8, 60, int(self.state.number_size),
                             lambda v: setattr(self.state, "number_size", float(v))),
            self._color_row("Renk", "number_color"),
            self._checkbox_row("Her numarada farklı renk", "number_multicolor"),
            self.lbl_next_number,
            reset_btn,
            self._hint("Sayfaya tıkladıkça sıradaki numara\n"
                      "yerleştirilir ve sayaç otomatik artar.\n"
                      "'Her numarada farklı renk' açıkken her\n"
                      "sayı HER ZAMAN aynı rengi alır."),
            self.chk_pair,
            reset_pair_btn,
            self._hint("Eşleştir modu — İKİ PDF karşılaştırırken:\n"
                      "1) Bir panele numara koy.\n"
                      "2) Diğer panele koy → aynı numara/renk.\n"
                      "3) Sayaç kendiliğinden artar.\n"
                      "Böylece iki belgede birebir eşleşen\n"
                      "çiftler (sol 5 ↔ sağ 5) oluşur."),
        ))
        self._tool_pages["number"] = idx; idx += 1

        self.prop_stack.addWidget(page(
            self._hint("🖼 Bir alan sürükleyip bırakın; açılan\n"
                      "pencereden PNG olarak kaydedebilir\n"
                      "ya da panoya kopyalayabilirsiniz.\n\n"
                      "Bu araç PDF'i DEĞİŞTİRMEZ — yalnızca\n"
                      "seçilen bölgeyi yüksek çözünürlükte\n"
                      "görsel olarak çıkarır. Ekran görüntüsü\n"
                      "almaktan daha nettir çünkü doğrudan\n"
                      "PDF'in kendi render motorunu kullanır."),
        ))
        self._tool_pages["crop"] = idx; idx += 1

        return self.prop_stack

    def _build_layers_tab(self):
        self.layers_list = QListWidget()
        self.layers_list.setObjectName("layers")
        self.layers_list.itemClicked.connect(self._on_layer_clicked)
        return self.layers_list

    def _build_shortcuts(self):
        def act(seq, fn):
            a = QAction(self)
            a.setShortcut(QKeySequence(seq))
            a.triggered.connect(fn)
            self.addAction(a)

        act("Ctrl+O", self.open_pdf)
        act("Ctrl+S", self.save_pdf)
        act("Ctrl+Z", self.undo_active)
        act("Ctrl+Y", self.redo_active)
        act("Ctrl+Shift+Z", self.redo_active)
        act("Right", lambda: self.goto_page(self.active_session.current_page + 1)
            if self.active_session else None)
        act("Left", lambda: self.goto_page(self.active_session.current_page - 1)
            if self.active_session else None)
        act(QKeySequence.StandardKey.Delete, self.delete_selected)

    # =====================================================================
    # Tema
    # =====================================================================
    _THEMES = {
        "dark": dict(
            bg="#1e1f22", fg="#e6e6e6", toolbar="#2b2d31", border="#111",
            label="#b9bbbe", status_fg="#9aa0a6", page_fg="#fff",
            btn="#3a3d43", btn_border="#4a4d55", btn_hover="#484c54",
            accent="#5865f2", accent_hover="#4752e0",
            slider_groove="#4a4d55", thumbs_bg="#232428", thumbs_item="#3a3d43",
            frame_bg="#26282c", view_bg="#2b2d31", hint="#8a8f98",
        ),
        "light": dict(
            bg="#f4f5f7", fg="#202225", toolbar="#ffffff", border="#d8dadf",
            label="#5a5f66", status_fg="#6a6f76", page_fg="#202225",
            btn="#eceef1", btn_border="#c9cdd3", btn_hover="#dfe2e6",
            accent="#5865f2", accent_hover="#4752e0",
            slider_groove="#c9cdd3", thumbs_bg="#eceef1", thumbs_item="#ffffff",
            frame_bg="#ffffff", view_bg="#dfe3e8", hint="#7a7f87",
        ),
    }

    def _stylesheet(self, t):
        return f"""
            QMainWindow, QWidget {{ background:{t['bg']}; color:{t['fg']};
                font-family:'Segoe UI','Arial'; font-size:13px; }}
            #toolbar {{ background:{t['toolbar']}; border-bottom:1px solid {t['border']}; }}
            #tbGroup {{ background:transparent; border-right:1px solid {t['border']}; }}
            #tbGroupTitle {{ color:{t['label']}; font-size:10px; font-weight:700; letter-spacing:1px; }}
            #sectionFrame {{ background:{t['frame_bg']}; border:1px solid {t['border']}; border-radius:8px; }}
            #sectionTitle {{ color:{t['label']}; font-size:10px; font-weight:700;
                letter-spacing:1px; padding:2px 2px 4px 2px; }}
            #hint {{ color:{t['hint']}; font-size:11px; }}
            #nextNumber {{ font-weight:700; color:{t['accent']}; }}
            QLabel {{ color:{t['label']}; }}
            #status {{ background:{t['toolbar']}; color:{t['status_fg']}; border-top:1px solid {t['border']}; }}
            #pagelbl {{ min-width:56px; qproperty-alignment:AlignCenter; color:{t['page_fg']}; }}
            QPushButton {{ background:{t['btn']}; border:1px solid {t['btn_border']};
                border-radius:6px; padding:6px 10px; color:{t['fg']}; }}
            QPushButton:hover {{ background:{t['btn_hover']}; }}
            QPushButton:disabled {{ color:{t['border']}; }}
            QPushButton#primary {{ background:{t['accent']}; border:none; font-weight:600; color:#fff; }}
            QPushButton#primary:hover {{ background:{t['accent_hover']}; }}
            QPushButton#colorbtn {{ border:1px solid {t['btn_border']}; border-radius:5px; }}
            QToolButton {{ background:{t['btn']}; border:1px solid {t['btn_border']};
                border-radius:6px; padding:6px 10px; color:{t['fg']}; }}
            QToolButton::menu-indicator {{ image:none; }}
            QToolButton:hover {{ background:{t['btn_hover']}; }}
            QToolButton#toolbtn:checked {{ background:{t['accent']}; border:1px solid {t['accent']};
                font-weight:700; color:#fff; }}
            QFrame#sep {{ color:{t['btn_border']}; max-width:1px; }}
            QSlider::groove:horizontal {{ height:4px; background:{t['slider_groove']}; border-radius:2px; }}
            QSlider::handle:horizontal {{ width:14px; background:{t['accent']}; border-radius:7px; margin:-6px 0; }}
            QCheckBox {{ color:{t['fg']}; }}
            QSpinBox {{ background:{t['btn']}; border:1px solid {t['btn_border']}; border-radius:5px;
                padding:3px; color:{t['fg']}; }}
            QComboBox {{ background:{t['btn']}; border:1px solid {t['btn_border']}; border-radius:5px;
                padding:3px; color:{t['fg']}; }}
            QListWidget#thumbs {{ background:{t['thumbs_bg']}; border:none; }}
            QListWidget#thumbs::item {{ background:{t['thumbs_item']};
                border:1px solid {t['btn_border']}; border-radius:4px; margin:2px; }}
            QListWidget#thumbs::item:selected {{ border:2px solid {t['accent']}; }}
            QListWidget#doclist {{ background:{t['frame_bg']}; border:none; }}
            QListWidget#doclist::item {{ border-bottom:1px solid {t['border']}; }}
            QListWidget#doclist::item:selected {{ background:{t['accent']}; }}
            QListWidget#layers {{ background:{t['frame_bg']}; border:none; }}
            QListWidget#layers::item {{ border-bottom:1px solid {t['border']}; }}
            QTabWidget::pane {{ border:1px solid {t['border']}; border-radius:8px; background:{t['frame_bg']}; }}
            QTabBar::tab {{ background:{t['btn']}; color:{t['fg']}; padding:6px 10px;
                border-top-left-radius:6px; border-top-right-radius:6px; }}
            QTabBar::tab:selected {{ background:{t['accent']}; color:#fff; }}
            QMenu {{ background:{t['toolbar']}; color:{t['fg']}; border:1px solid {t['border']}; }}
            QMenu::item:selected {{ background:{t['accent']}; color:#fff; }}
        """

    def _apply_style(self):
        self.dark_mode = True
        self.setStyleSheet(self._stylesheet(self._THEMES["dark"]))
        self.view.setBackgroundBrush(QColor(self._THEMES["dark"]["view_bg"]))

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        t = self._THEMES["dark" if self.dark_mode else "light"]
        self.setStyleSheet(self._stylesheet(t))
        self.view.setBackgroundBrush(QColor(t["view_bg"]))
        self.btn_theme.setText("☀ Açık Tema" if self.dark_mode else "🌙 Koyu Tema")

    def _set_ui_enabled(self, on):
        for w in (self.btn_export, self.btn_split, self.btn_prev, self.btn_next,
                  self.btn_fit, self.btn_add_page, self.btn_undo, self.btn_redo,
                  self.btn_compare):
            w.setEnabled(on)
        for _, b in self._tool_buttons:
            b.setEnabled(on)

    # =====================================================================
    # Araç / renk seçimi
    # =====================================================================
    def _select_tool(self, tool, btn):
        self.state.tool = tool
        for _, b in self._tool_buttons:
            b.setChecked(b is btn)
        if tool in self._tool_pages:
            self.prop_stack.setCurrentIndex(self._tool_pages[tool])
        if tool == "number":
            self._refresh_number_label()
        cursor = (Qt.CursorShape.IBeamCursor if tool in ("text", "number")
                  else Qt.CursorShape.CrossCursor if tool != "select"
                  else Qt.CursorShape.ArrowCursor)
        self.view.viewport().setCursor(cursor)

    def _pick_color(self, attr):
        current = getattr(self.state, attr)
        color = QColorDialog.getColor(current, self, "Renk seç")
        if color.isValid():
            setattr(self.state, attr, color)
            for btn in self._color_buttons_by_attr.get(attr, []):
                btn.setStyleSheet(f"background:{color.name()};")

    # =====================================================================
    # Belge oturumları (çoklu PDF)
    # =====================================================================
    def _open_path_as_session(self, path):
        pdf = PDFDocument(path)
        session = DocumentSession(pdf, path)
        self.sessions.append(session)
        return session

    def open_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "PDF Aç (birden fazla seçilebilir)",
                                                 "", "PDF (*.pdf)")
        if not paths:
            return
        last_idx = None
        for p in paths:
            try:
                self._open_path_as_session(p)
                last_idx = len(self.sessions) - 1
            except Exception as ex:
                QMessageBox.critical(self, "Hata", f"'{p}' açılamadı:\n{ex}")
        if last_idx is None:
            return
        self._set_ui_enabled(True)
        self.refresh_doc_list()
        self.switch_session(last_idx)

    def refresh_doc_list(self):
        self.doc_list.blockSignals(True)
        self.doc_list.clear()
        for i, s in enumerate(self.sessions):
            list_item = QListWidgetItem()
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 4, 4)
            lbl = QLabel(s.title)
            lbl.setWordWrap(True)
            h.addWidget(lbl, 1)
            btn = QPushButton("✕")
            btn.setFixedWidth(22)
            btn.setToolTip("Belgeyi kapat")
            btn.clicked.connect(lambda _, idx=i: self.close_session(idx))
            h.addWidget(btn)
            list_item.setSizeHint(row.sizeHint())
            self.doc_list.addItem(list_item)
            self.doc_list.setItemWidget(list_item, row)
        if self.active_session in self.sessions:
            self.doc_list.setCurrentRow(self.sessions.index(self.active_session))
        self.doc_list.blockSignals(False)

    def _on_doclist_selected(self, row):
        if 0 <= row < len(self.sessions) and self.sessions[row] is not self.active_session:
            self.switch_session(row)

    def _on_doclist_context_menu(self, pos):
        row = self.doc_list.indexAt(pos).row()
        if not (0 <= row < len(self.sessions)):
            return
        session = self.sessions[row]
        menu = QMenu(self)
        act_folder = menu.addAction("📂 Klasörde Göster")
        act_rename = menu.addAction("🔤 Sekme Adını Değiştir")
        act_copy = menu.addAction("📋 Dosya Yolunu Kopyala")
        menu.addSeparator()
        act_close = menu.addAction("✕ Kapat")
        chosen = menu.exec(self.doc_list.viewport().mapToGlobal(pos))

        if chosen == act_folder:
            folder = os.path.dirname(os.path.abspath(session.path))
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
                QMessageBox.warning(self, "Uyarı", f"Klasör açılamadı:\n{folder}")
        elif chosen == act_rename:
            new_name, ok = QInputDialog.getText(self, "Sekme Adını Değiştir",
                                                "Yeni ad:", text=session.title)
            if ok and new_name.strip():
                session.title = new_name.strip()
                self.refresh_doc_list()
                if session is self.active_session:
                    self.setWindowTitle(f"PDF İşaretleme Aracı — {session.title}")
        elif chosen == act_copy:
            QApplication.clipboard().setText(session.path)
            self.status.setText("Dosya yolu panoya kopyalandı.")
        elif chosen == act_close:
            self.close_session(row)

    def switch_session(self, idx):
        self.active_session = self.sessions[idx]
        self._build_thumbnails()
        self.goto_page(self.active_session.current_page)
        self.setWindowTitle(f"PDF İşaretleme Aracı — {self.active_session.title}")
        self.update_history_buttons()
        self._refresh_number_label()
        self.refresh_doc_list()
        if hasattr(self, "pane_left"):
            self.pane_left.refresh_sessions()
            self.pane_right.refresh_sessions()

    def close_session(self, idx):
        if not (0 <= idx < len(self.sessions)):
            return
        closing = self.sessions[idx]
        was_active = closing is self.active_session
        self.sessions.pop(idx)
        # Kapatılan belgeye ait geri-al kayıtlarını global sıradan çıkar.
        self._global_undo = [s for s in self._global_undo if s is not closing]
        self._global_redo = [s for s in self._global_redo if s is not closing]
        if self._pair_pending and self._pair_pending[0].session is closing:
            self._pair_pending = None
            self._refresh_number_label()
        if not self.sessions:
            self.active_session = None
            self._set_ui_enabled(False)
            self.thumb_list.clear()
            self.view.scene_obj.clear()
            self.lbl_page.setText("0 / 0")
            self.status.setText("Tüm belgeler kapatıldı.")
            self.refresh_doc_list()
            self.update_history_buttons()
            return
        if was_active:
            new_idx = min(idx, len(self.sessions) - 1)
            self.refresh_doc_list()
            self.switch_session(new_idx)
        else:
            self.refresh_doc_list()
        self.update_history_buttons()

    # =====================================================================
    # Dosya: kaydet / dışa aktar / birleştir / ayır
    # =====================================================================
    def save_pdf(self):
        s = self.active_session
        if not s:
            return
        default = os.path.splitext(s.path)[0] + "_isaretli.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "PDF Olarak Kaydet", default, "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            s.pdf.export_pdf(path, s.annotations, self.font_ctx)
            self.status.setText(f"Kaydedildi: {path}")
            QMessageBox.information(self, "Tamam", "PDF başarıyla kaydedildi.")
        except Exception as ex:
            QMessageBox.critical(self, "Hata", f"Kaydedilemedi:\n{ex}")

    def export_image_current(self, ext):
        s = self.active_session
        if not s:
            return
        filt = "PNG Görseli (*.png)" if ext == "png" else "JPEG Görseli (*.jpg)"
        default = os.path.splitext(s.path)[0] + f"_sayfa{s.current_page + 1}.{ext}"
        path, _ = QFileDialog.getSaveFileName(self, "Görsel Olarak Dışa Aktar", default, filt)
        if not path:
            return
        if not path.lower().endswith("." + ext):
            path += "." + ext
        try:
            s.pdf.export_image(s.current_page, s.annotations[s.current_page], self.font_ctx, path)
            self.status.setText(f"Dışa aktarıldı: {path}")
        except Exception as ex:
            QMessageBox.critical(self, "Hata", f"Dışa aktarılamadı:\n{ex}")

    def export_all_pages_images(self, ext):
        s = self.active_session
        if not s:
            return
        folder = QFileDialog.getExistingDirectory(self, "Klasör Seç")
        if not folder:
            return
        base = os.path.splitext(os.path.basename(s.path))[0]
        try:
            for i in range(s.pdf.page_count):
                out = os.path.join(folder, f"{base}_sayfa{i + 1}.{ext}")
                s.pdf.export_image(i, s.annotations.get(i, []), self.font_ctx, out)
            self.status.setText(f"Tüm sayfalar dışa aktarıldı: {folder}")
            QMessageBox.information(self, "Tamam",
                                    f"{s.pdf.page_count} sayfa '{folder}' klasörüne kaydedildi.")
        except Exception as ex:
            QMessageBox.critical(self, "Hata", f"Dışa aktarılamadı:\n{ex}")

    def merge_pdfs(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Birleştirilecek PDF'leri seçin (sırayla)", "", "PDF (*.pdf)")
        if len(files) < 2:
            if files:
                QMessageBox.information(self, "Bilgi", "Birleştirmek için en az 2 PDF seçin.")
            return
        out_path, _ = QFileDialog.getSaveFileName(self, "Birleşik PDF'i Kaydet",
                                                   "birlesik.pdf", "PDF (*.pdf)")
        if not out_path:
            return
        if not out_path.lower().endswith(".pdf"):
            out_path += ".pdf"
        try:
            merged = fitz.open()
            for f in files:
                merged.insert_pdf(fitz.open(f))
            merged.save(out_path)
            merged.close()
            self._open_path_as_session(out_path)
            self._set_ui_enabled(True)
            self.refresh_doc_list()
            self.switch_session(len(self.sessions) - 1)
            self.status.setText(f"{len(files)} PDF birleştirildi: {out_path}")
        except Exception as ex:
            QMessageBox.critical(self, "Hata", f"Birleştirilemedi:\n{ex}")

    def split_all_pages(self):
        s = self.active_session
        if not s:
            return
        folder = QFileDialog.getExistingDirectory(self, "Klasör Seç")
        if not folder:
            return
        base = os.path.splitext(os.path.basename(s.path))[0]
        try:
            annotated = s.pdf._annotated_copy(s.annotations, self.font_ctx)
            for i in range(annotated.page_count):
                single = fitz.open()
                single.insert_pdf(annotated, from_page=i, to_page=i)
                single.save(os.path.join(folder, f"{base}_sayfa{i + 1}.pdf"))
                single.close()
            annotated.close()
            self.status.setText(f"{s.pdf.page_count} sayfa ayrı PDF olarak kaydedildi: {folder}")
            QMessageBox.information(self, "Tamam", f"Sayfalar '{folder}' klasörüne kaydedildi.")
        except Exception as ex:
            QMessageBox.critical(self, "Hata", f"Ayrılamadı:\n{ex}")

    def extract_page_range(self):
        s = self.active_session
        if not s:
            return
        n = s.pdf.page_count
        start, ok1 = QInputDialog.getInt(self, "Aralık Çıkar", "Başlangıç sayfa no:", 1, 1, n)
        if not ok1:
            return
        end, ok2 = QInputDialog.getInt(self, "Aralık Çıkar", "Bitiş sayfa no:", n, start, n)
        if not ok2:
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Aralığı Yeni PDF Olarak Kaydet",
            os.path.splitext(s.path)[0] + f"_s{start}-{end}.pdf", "PDF (*.pdf)")
        if not out_path:
            return
        try:
            annotated = s.pdf._annotated_copy(s.annotations, self.font_ctx)
            part = fitz.open()
            part.insert_pdf(annotated, from_page=start - 1, to_page=end - 1)
            part.save(out_path)
            part.close()
            annotated.close()
            self.status.setText(f"{start}-{end}. sayfalar kaydedildi: {out_path}")
        except Exception as ex:
            QMessageBox.critical(self, "Hata", f"Çıkarılamadı:\n{ex}")

    # =====================================================================
    # Sayfa yönetimi (ekle / sil / kopyala / taşı)
    # =====================================================================
    def _reindex_annotations(self, session, old_count, transform):
        order = [session.annotations.get(i, []) for i in range(old_count)]
        order = transform(order)
        session.annotations = {i: lst for i, lst in enumerate(order)}
        # Sayfa yapısı değişti; bu belgeye ait eski geri-al kayıtları artık
        # geçersiz sayfa indekslerine işaret edebilir. O session'ın hem kendi
        # geçmişini hem global sıradaki girişlerini temizliyoruz.
        session.history.clear()
        self._global_undo = [s for s in self._global_undo if s is not session]
        self._global_redo = [s for s in self._global_redo if s is not session]
        self.update_history_buttons()

    def _delete_page(self, idx):
        s = self.active_session
        if not s:
            return
        if s.pdf.page_count <= 1:
            QMessageBox.warning(self, "Uyarı", "Belgede tek sayfa var, silinemez.")
            return
        old_count = s.pdf.page_count
        s.pdf.delete_page(idx)
        self._reindex_annotations(s, old_count, lambda order: (order.pop(idx), order)[1])
        s.current_page = min(s.current_page, s.pdf.page_count - 1)
        self._build_thumbnails()
        self.goto_page(s.current_page)
        self.status.setText(f"{idx + 1}. sayfa silindi.")

    def _insert_blank_page(self, idx):
        s = self.active_session
        if not s:
            return
        old_count = s.pdf.page_count
        w, h = s.pdf.page_size(0) if old_count else (595, 842)
        idx = max(0, min(idx, old_count))
        s.pdf.insert_blank_page(idx, w, h)
        self._reindex_annotations(s, old_count, lambda order: (order.insert(idx, []), order)[1])
        self._build_thumbnails()
        self.goto_page(idx)
        self.status.setText("Boş sayfa eklendi.")

    def _duplicate_page(self, idx):
        s = self.active_session
        if not s:
            return
        old_count = s.pdf.page_count
        s.pdf.duplicate_page(idx)

        def _t(order):
            order.insert(idx + 1, copy.deepcopy(order[idx]))
            return order

        self._reindex_annotations(s, old_count, _t)
        self._build_thumbnails()
        self.goto_page(idx + 1)
        self.status.setText("Sayfa kopyalandı.")

    def _move_page(self, frm, to):
        s = self.active_session
        if not s:
            return
        old_count = s.pdf.page_count
        s.pdf.move_page(frm, to)

        def _t(order):
            item = order.pop(frm)
            order.insert(to, item)
            return order

        self._reindex_annotations(s, old_count, _t)
        self._build_thumbnails()
        self.goto_page(to)
        self.status.setText("Sayfa taşındı.")

    def _on_thumb_context_menu(self, pos):
        s = self.active_session
        if not s:
            return
        row = self.thumb_list.indexAt(pos).row()
        if row < 0:
            return
        menu = QMenu(self)
        act_before = menu.addAction("➕ Öncesine Boş Sayfa Ekle")
        act_after = menu.addAction("➕ Sonrasına Boş Sayfa Ekle")
        act_dup = menu.addAction("📄 Sayfayı Kopyala")
        menu.addSeparator()
        act_up = menu.addAction("⬆ Yukarı Taşı")
        act_down = menu.addAction("⬇ Aşağı Taşı")
        menu.addSeparator()
        act_del = menu.addAction("🗑 Sayfayı Sil")
        chosen = menu.exec(self.thumb_list.viewport().mapToGlobal(pos))
        if chosen == act_before:
            self._insert_blank_page(row)
        elif chosen == act_after:
            self._insert_blank_page(row + 1)
        elif chosen == act_dup:
            self._duplicate_page(row)
        elif chosen == act_up and row > 0:
            self._move_page(row, row - 1)
        elif chosen == act_down and row < s.pdf.page_count - 1:
            self._move_page(row, row + 1)
        elif chosen == act_del:
            self._delete_page(row)

    # =====================================================================
    # Küçük resimler / sayfa gösterimi
    # =====================================================================
    def _build_thumbnails(self):
        s = self.active_session
        self.thumb_list.blockSignals(True)
        self.thumb_list.clear()
        if s:
            for i in range(s.pdf.page_count):
                pix = s.pdf.thumbnail(i)
                item = QListWidgetItem(QIcon(pix), f"{i + 1}")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.thumb_list.addItem(item)
        self.thumb_list.blockSignals(False)

    def _on_thumb_selected(self, row):
        if row >= 0 and self.active_session and row != self.active_session.current_page:
            self.goto_page(row)

    def goto_page(self, index):
        s = self.active_session
        if not s:
            return
        index = max(0, min(index, s.pdf.page_count - 1))
        s.current_page = index
        self.view.page_lines = s.pdf.text_lines(index)
        self._render_page()
        self.lbl_page.setText(f"{index + 1} / {s.pdf.page_count}")
        if self.thumb_list.currentRow() != index:
            self.thumb_list.blockSignals(True)
            self.thumb_list.setCurrentRow(index)
            self.thumb_list.blockSignals(False)

    def _render_page(self):
        s = self.active_session
        scene = self.view.scene_obj
        scene.clear()
        self.item_ann_map.clear()
        if not s:
            self.refresh_layers_panel()
            return

        pix = s.pdf.render_pixmap(s.current_page)
        item = scene.addPixmap(pix)
        item.setScale(1.0 / RENDER_SCALE)
        w, h = s.pdf.page_size(s.current_page)
        scene.setSceneRect(0, 0, w, h)
        item.setZValue(-1)

        for ann in s.annotations[s.current_page]:
            gitem = ann.create_item()
            gitem.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                           QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
            scene.addItem(gitem)
            self.item_ann_map[gitem] = ann

        self.refresh_layers_panel()

    def fit_width(self):
        s = self.active_session
        if not s:
            return
        w, _ = s.pdf.page_size(s.current_page)
        vp = self.view.viewport().width() - 24
        if w > 0:
            self.view.resetTransform()
            self.view.scale(vp / w, vp / w)

    # =====================================================================
    # Annotation ekle / sil / taşı
    # =====================================================================
    def _record_add(self, session, page, ann, on_undo=None, on_redo=None):
        """Bir ekleme işlemini hem session geçmişine hem global sıraya kaydeder.
        Global sıra, karşılaştırmada başka belgeye eklenen numaraların da
        doğru belgede geri alınmasını sağlar."""
        session.history.push_add(page, ann, on_undo=on_undo, on_redo=on_redo)
        self._global_undo.append(session)
        self._global_redo.clear()

    def add_annotation(self, ann):
        s = self.active_session
        if not s:
            return
        s.annotations[s.current_page].append(ann)
        self._record_add(s, s.current_page, ann)
        self._render_page()
        self.update_history_buttons()
        self.status.setText("İşaret eklendi.")

    def on_text_requested(self, scene_pt):
        text, ok = QInputDialog.getMultiLineText(self, "Metin Ekle", "Yazı:", "")
        if ok and text.strip():
            ann = TextAnnotation(scene_pt.x(), scene_pt.y(), text,
                                 self.state.text_color, self.state.font_size)
            self.add_annotation(ann)

    def on_number_requested(self, scene_pt):
        """Ana görünümde numaralandırma. Sayaç, geri alınınca da bir azalır."""
        s = self.active_session
        if not s:
            return
        if s.number_counter is None:
            s.number_counter = int(self.state.number_start)
        n = s.number_counter
        color = number_to_color(n) if self.state.number_multicolor else self.state.number_color
        ann = NumberAnnotation(scene_pt.x(), scene_pt.y(), n, color, self.state.number_size)
        s.annotations[s.current_page].append(ann)

        # Sayaç geri-al/yinele: numarayı geri alınca sayaç da geri gitsin.
        def on_undo():
            s.number_counter = n
            self._refresh_number_label()

        def on_redo():
            s.number_counter = n + 1
            self._refresh_number_label()

        self._record_add(s, s.current_page, ann, on_undo=on_undo, on_redo=on_redo)
        s.number_counter = n + 1
        self._render_page()
        self.update_history_buttons()
        self._refresh_number_label()
        self.status.setText(f"Numara {n} yerleştirildi.")

    def _place_number(self, session, page, scene_pt, n, color, on_undo=None, on_redo=None):
        """Belirtilen session/sayfaya numara ekler ve ilgili tüm görünümleri
        günceller (ana görünüm + iki karşılaştırma paneli)."""
        ann = NumberAnnotation(scene_pt.x(), scene_pt.y(), n, color, self.state.number_size)
        session.annotations.setdefault(page, []).append(ann)
        self._record_add(session, page, ann, on_undo=on_undo, on_redo=on_redo)
        self._refresh_pane_and_page(session, page)
        self.update_history_buttons()

    def _refresh_pane_and_page(self, session, page):
        """Bir session/sayfadaki değişikliği hem karşılaştırma panellerine
        hem (uygunsa) ana görünüme yansıtır."""
        for pane in (self.pane_left, self.pane_right):
            if pane.session is session and pane.page == page:
                pane._render()
        if self.active_session is session and self.active_session.current_page == page:
            self._render_page()

    def on_number_requested_in_pane(self, pane, scene_pt):
        """Karşılaştırma modundaki bir panelde numaralandırma.

        İki çalışma biçimi var:
        - Normal: her tıklama, o panelin belgesinin kendi sayacından sıradaki
          numarayı koyar (iki belge bağımsız sayılır).
        - Eşleştir modu (number_pair_mode): bir panele koyunca numara/renk
          KİLİTLENİR; diğer panele koyunca AYNI numara/renk kullanılır ve çift
          tamamlanınca ortak sayaç bir artar (solda 5 -> sağda da 5).

        Her iki biçimde de numarayı geri alınca (Ctrl+Z) ilgili sayaç da geri
        gider."""
        session = pane.session
        if session is None:
            return
        page = pane.page

        if not self.state.number_pair_mode:
            if session.number_counter is None:
                session.number_counter = int(self.state.number_start)
            n = session.number_counter
            color = number_to_color(n) if self.state.number_multicolor else self.state.number_color

            def on_undo():
                session.number_counter = n
                self._refresh_number_label()

            def on_redo():
                session.number_counter = n + 1
                self._refresh_number_label()

            self._place_number(session, page, scene_pt, n, color, on_undo, on_redo)
            session.number_counter = n + 1
            self._refresh_number_label()
            self.status.setText(f"Numara {n} yerleştirildi.")
            return

        # --- Eşleştir modu ---
        pend = self._pair_pending   # None ya da (pane, n, color)
        if pend is None:
            # Çiftin ilk yarısı: ortak sayaçtan numara al, kilitle.
            n = self._pair_counter
            color = number_to_color(n) if self.state.number_multicolor else self.state.number_color

            def on_undo():
                # İlk yarı geri alınırsa: bekleyen çifti iptal et.
                self._pair_pending = None
                self._refresh_number_label()

            def on_redo():
                self._pair_pending = (pane, n, color)
                self._refresh_number_label()

            self._place_number(session, page, scene_pt, n, color, on_undo, on_redo)
            self._pair_pending = (pane, n, color)
            self._refresh_number_label()
            self.status.setText(f"Eşleştirme: {n} numarası kondu, aynısını diğer panele koyun.")
        else:
            first_pane, n, color = pend
            if pane is first_pane:
                self.status.setText("Önce DİĞER panele aynı numarayı koyun (ya da Eşleştir'i kapatın).")
                return
            # Çiftin ikinci yarısı: AYNI numara/renk, sayaç artar.
            def on_undo():
                # İkinci yarı geri alınırsa: sayaç geri gitsin, çift yeniden
                # 'yarım' hale gelsin (ilk yarı hâlâ duruyor).
                self._pair_counter = n
                self._pair_pending = (first_pane, n, color)
                self._refresh_number_label()

            def on_redo():
                self._pair_counter = n + 1
                self._pair_pending = None
                self._refresh_number_label()

            self._place_number(session, page, scene_pt, n, color, on_undo, on_redo)
            self._pair_pending = None
            self._pair_counter = n + 1
            self._refresh_number_label()
            self.status.setText(f"{n} numaralı çift tamamlandı. Sıradaki: {self._pair_counter}")

    def reset_pair_counter(self):
        self._pair_counter = int(self.state.number_start)
        self._pair_pending = None
        self._refresh_number_label()
        self.status.setText(f"Eşleştirme sayacı sıfırlandı. Sıradaki: {self._pair_counter}")

    def _on_number_start_changed(self, v):
        self.state.number_start = v
        if not self.state.number_pair_mode and self.active_session:
            # başlangıç değişince eşleştirme sayacını da hizala (henüz
            # başlamadıysa)
            pass
        self._refresh_number_label()

    def _on_pair_toggled(self, checked):
        self.state.number_pair_mode = checked
        self._pair_pending = None
        if checked:
            self._pair_counter = int(self.state.number_start)
        self._refresh_number_label()

    def reset_number_counter(self):
        if self.active_session:
            self.active_session.number_counter = int(self.state.number_start)
            self._refresh_number_label()

    def _refresh_number_label(self):
        if not hasattr(self, "lbl_next_number"):
            return
        if self.state.number_pair_mode:
            self.lbl_next_number.setText(f"Sıradaki (eşleştirme): {self._pair_counter}")
            return
        s = self.active_session
        if s is None:
            self.lbl_next_number.setText("Sıradaki: —")
            return
        n = s.number_counter if s.number_counter is not None else int(self.state.number_start)
        self.lbl_next_number.setText(f"Sıradaki: {n}")

    def on_crop_requested(self, rect: QRectF):
        """PDF'i değiştirmeden, seçilen bölgeyi yüksek çözünürlükte
        (doğrudan PDF render motorundan, ekran görüntüsü değil) çıkarır."""
        s = self.active_session
        if not s:
            return
        try:
            clip = fitz.Rect(rect.left(), rect.top(), rect.right(), rect.bottom())
            page = s.pdf.doc[s.current_page]
            pix = page.get_pixmap(matrix=fitz.Matrix(4.0, 4.0), clip=clip, alpha=False)
            qimg = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format.Format_RGB888).copy()
        except Exception as ex:
            QMessageBox.critical(self, "Hata", f"Kırpılamadı:\n{ex}")
            return
        self._show_crop_dialog(qimg)

    def _show_crop_dialog(self, qimage: QImage):
        dlg = QDialog(self)
        dlg.setWindowTitle("Kırpılan Alan")
        v = QVBoxLayout(dlg)

        pm = QPixmap.fromImage(qimage)
        preview = QLabel()
        max_w = 480
        preview.setPixmap(pm.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)
                          if pm.width() > max_w else pm)
        v.addWidget(preview)

        info = QLabel(f"{pm.width()} × {pm.height()} piksel")
        info.setObjectName("hint")
        v.addWidget(info)

        row = QHBoxLayout()
        btn_copy = QPushButton("📋 Panoya Kopyala")
        btn_save = QPushButton("💾 PNG Olarak Kaydet")
        btn_close = QPushButton("Kapat")
        row.addWidget(btn_copy); row.addWidget(btn_save); row.addWidget(btn_close)
        v.addLayout(row)

        def do_copy():
            QApplication.clipboard().setImage(qimage)
            self.status.setText("Kırpılan alan panoya kopyalandı.")

        def do_save():
            path, _ = QFileDialog.getSaveFileName(self, "Kırpılan Alanı Kaydet",
                                                  "kirpma.png", "PNG (*.png)")
            if path:
                if not path.lower().endswith(".png"):
                    path += ".png"
                qimage.save(path)
                self.status.setText(f"Kaydedildi: {path}")

        btn_copy.clicked.connect(do_copy)
        btn_save.clicked.connect(do_save)
        btn_close.clicked.connect(dlg.accept)
        dlg.exec()

    def undo_active(self):
        """Global sıradaki EN SON işlemi, yapıldığı belgede geri alır.
        Böylece karşılaştırma modunda başka belgeye eklenmiş bir numara da
        doğru belgede geri alınır (yalnızca aktif belgeye bakılmaz)."""
        if not self._global_undo:
            return
        session = self._global_undo.pop()
        page = session.history.undo()
        self._global_redo.append(session)
        if page is not None:
            self._after_history_change(session, page)

    def redo_active(self):
        if not self._global_redo:
            return
        session = self._global_redo.pop()
        page = session.history.redo()
        self._global_undo.append(session)
        if page is not None:
            self._after_history_change(session, page)

    def _after_history_change(self, session, page):
        """Undo/redo sonrası ekranı tazeler. İşlem hangi belgede olduysa
        gerekiyorsa o belgeye/sayfaya geçer, karşılaştırma panellerini de
        günceller."""
        if self.canvas_stack.currentIndex() == 0:
            # Normal görünüm: işlem başka belgedeyse ona geç
            if session is not self.active_session:
                idx = self.sessions.index(session)
                self.switch_session(idx)
            self.goto_page(page)
        else:
            # Karşılaştırma görünümü: panelleri tazele
            self._refresh_pane_and_page(session, page)
            # aktif belge de aynıysa ana sahne arka planda güncel kalsın
            if self.active_session is session:
                self.active_session.current_page = page
        self.update_history_buttons()

    def sync_moved_items(self):
        s = self.active_session
        if not s:
            return
        moved = False
        for gitem, ann in list(self.item_ann_map.items()):
            pos = gitem.pos()
            if pos.x() != 0 or pos.y() != 0:
                dx, dy = pos.x(), pos.y()
                ann.translate(dx, dy)
                s.history.push_move(s.current_page, ann, dx, dy)
                self._global_undo.append(s)
                self._global_redo.clear()
                moved = True
        if moved:
            self._render_page()
            self.update_history_buttons()
            self.status.setText("Öge taşındı.")

    def _delete_item(self, gitem):
        s = self.active_session
        ann = self.item_ann_map.get(gitem)
        if not s or ann is None:
            return
        s.annotations[s.current_page].remove(ann)
        s.history.push_remove(s.current_page, ann)
        self._global_undo.append(s)
        self._global_redo.clear()
        self._render_page()
        self.update_history_buttons()
        self.status.setText("Öge silindi.")

    def delete_selected(self):
        for gitem in self.view.scene_obj.selectedItems():
            self._delete_item(gitem)

    def show_item_context_menu(self, gitem, global_pos):
        if gitem is None or gitem not in self.item_ann_map:
            return
        menu = QMenu(self)
        act_front = menu.addAction("⬆ En Öne Getir")
        act_back = menu.addAction("⬇ En Arkaya Gönder")
        menu.addSeparator()
        act_del = menu.addAction("🗑 Sil")
        chosen = menu.exec(global_pos)
        if chosen == act_del:
            self._delete_item(gitem)
        elif chosen == act_front:
            self._reorder(gitem, to_front=True)
        elif chosen == act_back:
            self._reorder(gitem, to_front=False)

    def _reorder(self, gitem, to_front):
        s = self.active_session
        ann = self.item_ann_map.get(gitem)
        if not s or ann is None:
            return
        lst = s.annotations[s.current_page]
        if ann in lst:
            lst.remove(ann)
            lst.append(ann) if to_front else lst.insert(0, ann)
        self._render_page()

    # =====================================================================
    # Katmanlar paneli
    # =====================================================================
    def _ann_label(self, ann):
        if isinstance(ann, TextAnnotation):
            return f"🅣  {ann.text.replace(chr(10), ' ')[:16]}"
        if isinstance(ann, InkAnnotation):
            return "✎  Kalem çizimi"
        if isinstance(ann, LineAnnotation):
            return "↗  Çizgi / Ok"
        if isinstance(ann, ShapeAnnotation):
            return "▭  Dörtgen" if ann.kind == "rect" else "◯  Oval"
        if isinstance(ann, HighlightAnnotation):
            return "🖍  Fosforlu"
        if isinstance(ann, RedactionAnnotation):
            return "⬛  Sansür (kalıcı)"
        if isinstance(ann, NumberAnnotation):
            return f"🔢  Numara {ann.number}"
        return "Öge"

    def refresh_layers_panel(self):
        if not hasattr(self, "layers_list"):
            return
        self.layers_list.clear()
        s = self.active_session
        anns = s.annotations.get(s.current_page, []) if s else []
        for ann in anns:
            list_item = QListWidgetItem()
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 4, 4)
            lbl = QLabel(self._ann_label(ann))
            h.addWidget(lbl, 1)
            btn = QPushButton("🗑")
            btn.setFixedWidth(28)
            btn.clicked.connect(lambda _, a=ann: self._delete_annotation_by_ref(a))
            h.addWidget(btn)
            list_item.setData(Qt.ItemDataRole.UserRole, ann)
            list_item.setSizeHint(row.sizeHint())
            self.layers_list.addItem(list_item)
            self.layers_list.setItemWidget(list_item, row)
        if not anns:
            empty = QListWidgetItem("Bu sayfada henüz işaret yok.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.layers_list.addItem(empty)

    def _on_layer_clicked(self, list_item):
        ann = list_item.data(Qt.ItemDataRole.UserRole)
        if ann is None:
            return
        for gitem, a in self.item_ann_map.items():
            gitem.setSelected(a is ann)
            if a is ann:
                self.view.centerOn(gitem)

    def _delete_annotation_by_ref(self, ann):
        gitem = None
        for k, v in self.item_ann_map.items():
            if v is ann:
                gitem = k
                break
        if gitem is not None:
            self._delete_item(gitem)

    def update_history_buttons(self):
        self.btn_undo.setEnabled(bool(self._global_undo))
        self.btn_redo.setEnabled(bool(self._global_redo))

    # =====================================================================
    # Karşılaştırma modu
    # =====================================================================
    def toggle_compare_mode(self, checked):
        self.canvas_stack.setCurrentIndex(1 if checked else 0)
        # Karşılaştırma modunda iki farklı belge/sayfa aynı anda gösterildiği
        # için genel çizim araçları anlamsızlaşır ve kapatılır — ANCAK
        # 'Numarala' özellikle açık bırakılır, çünkü karşılaştırmalı
        # numaralandırmanın asıl amacı budur (iki belge arasında karşılık
        # gelen noktaları işaretlemek).
        for key, b in self._tool_buttons:
            if checked:
                b.setEnabled(key == "number")
            else:
                b.setEnabled(self.active_session is not None)
        if checked:
            if self.state.tool != "number":
                self._select_tool("number", dict(self._tool_buttons)["number"])
            self.pane_left.refresh_sessions()
            self.pane_right.refresh_sessions()
            if len(self.sessions) > 1 and self.pane_right.combo.count() > 1:
                self.pane_right.combo.setCurrentIndex(1)
        else:
            self._select_tool("select", self.btn_select)



def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
