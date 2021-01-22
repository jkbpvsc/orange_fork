from collections import namedtuple
from itertools import chain, count
from typing import List, Optional, Tuple

import numpy as np
from scipy import stats
from sklearn.neighbors import KernelDensity

from AnyQt.QtCore import QItemSelection, QPointF, QRectF, QSize, Qt, Signal
from AnyQt.QtGui import QBrush, QColor, QPainter, QPainterPath, QPolygonF
from AnyQt.QtWidgets import QCheckBox, QSizePolicy, QWidget

import pyqtgraph as pg

from orangewidget.utils.listview import ListViewSearch
from orangewidget.utils.visual_settings_dlg import KeyType, ValueType, \
    VisualSettingsDialog

from Orange.data import ContinuousVariable, DiscreteVariable, Table
from Orange.widgets import gui
from Orange.widgets.settings import ContextSetting, DomainContextHandler, \
    Setting
from Orange.widgets.utils.annotated_data import ANNOTATED_DATA_SIGNAL_NAME, \
    create_annotated_table
from Orange.widgets.utils.itemmodels import VariableListModel
from Orange.widgets.utils.plot import PANNING, SELECT, ZOOMING
from Orange.widgets.utils.sql import check_sql_input
from Orange.widgets.utils.state_summary import format_summary_details
from Orange.widgets.visualize.owboxplot import SortProxyModel
from Orange.widgets.visualize.utils.customizableplot import \
    CommonParameterSetter, Updater
from Orange.widgets.visualize.utils.plotutils import AxisItem
from Orange.widgets.widget import OWWidget, Input, Output, Msg


class ViolinPlotViewBox(pg.ViewBox):  # TODO
    def __init__(self, parent):
        super().__init__()
        self.graph = parent
        self.setMouseMode(self.RectMode)

    def mouseDragEvent(self, ev, axis=None):
        if self.graph.state == SELECT and axis is None:
            ev.accept()
            if ev.button() == Qt.LeftButton:
                self.updateScaleBox(ev.buttonDownPos(), ev.pos())
                if ev.isFinish():
                    self.rbScaleBox.hide()
                    p1, p2 = ev.buttonDownPos(ev.button()), ev.pos()
                    p1 = self.mapToView(p1)
                    p2 = self.mapToView(p2)
                    self.graph.select_by_rectangle(QRectF(p1, p2))
                else:
                    self.updateScaleBox(ev.buttonDownPos(), ev.pos())
        elif self.graph.state == ZOOMING or self.graph.state == PANNING:
            super().mouseDragEvent(ev, axis=axis)
        else:
            ev.ignore()

    def mouseClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.graph.select_by_click(self.mapSceneToView(ev.scenePos()))
            ev.accept()


class ParameterSetter(CommonParameterSetter):
    def __init__(self, master):
        self.master: ViolinPlot = master
        super().__init__()

    def update_setters(self):
        def update_titles(**settings):
            self.titles_settings.update(**settings)
            Updater.update_axes_titles_font(self.axis_items, **settings)

        def update_ticks(**settings):
            self.ticks_settings.update(**settings)
            Updater.update_axes_ticks_font(self.axis_items, **settings)

        self.titles_settings = {}
        self.ticks_settings = {}

        self._setters[self.LABELS_BOX][self.AXIS_TITLE_LABEL] = update_titles
        self._setters[self.LABELS_BOX][self.AXIS_TICKS_LABEL] = update_ticks

        self.initial_settings = {
            self.LABELS_BOX: {
                self.FONT_FAMILY_LABEL: self.FONT_FAMILY_SETTING,
                self.TITLE_LABEL: self.FONT_SETTING,
                self.AXIS_TITLE_LABEL: self.FONT_SETTING,
                self.AXIS_TICKS_LABEL: self.FONT_SETTING,
            },
            self.ANNOT_BOX: {
                self.TITLE_LABEL: {self.TITLE_LABEL: ("", "")},
            },
        }

    @property
    def title_item(self):
        return self.master.getPlotItem().titleLabel

    @property
    def axis_items(self):
        return [value["item"] for value in
                self.master.getPlotItem().axes.values()]


def fit_kernel(data: np.ndarray, kernel: str) -> \
        Tuple[Optional[KernelDensity], float]:
    assert np.all(np.isfinite(data))

    if data.size < 2:
        return None, 1

    # obtain bandwidth
    try:
        kde = stats.gaussian_kde(data)
        bw = kde.factor * data.std(ddof=1)
    except np.linalg.LinAlgError:
        bw = 1

    # fit selected kernel
    kde = KernelDensity(bandwidth=bw, kernel=kernel)
    kde.fit(data.reshape(-1, 1))
    return kde, bw


class ViolinItem(pg.GraphicsObject):
    RugPlot = namedtuple("RugPlot", "support, density")

    def __init__(self, data: np.ndarray, color: QColor, kernel: str,
                 show_rug: bool, orientation: Qt.Orientations):
        self.__show_rug_plot = show_rug
        self.__orientation = orientation

        kde, bw = fit_kernel(data, kernel)
        self.__kde: KernelDensity = kde
        self.__bandwidth: float = bw

        self.__violin_path: QPainterPath = self._create_violin(data)
        self.__violin_brush: QBrush = QBrush(color)

        self.__rug_plot_data: ViolinItem.RugPlot = self._create_rug_plot(data)

        super().__init__()

    @property
    def violin_width(self):
        return -self.boundingRect().x() if self.__orientation == Qt.Vertical \
            else -self.boundingRect().y()

    def set_show_rug_plot(self, show: bool):
        self.__show_rug_plot = show
        self.update()

    def boundingRect(self) -> QRectF:
        return self.__violin_path.boundingRect()

    def paint(self, painter: QPainter, *_):
        painter.save()
        painter.setPen(pg.mkPen(QColor(Qt.black)))
        painter.setBrush(self.__violin_brush)
        painter.drawPath(self.__violin_path)

        if self.__show_rug_plot:
            data, density = self.__rug_plot_data
            painter.setPen(pg.mkPen(QColor(Qt.black), width=1))
            for x, y in zip(density, data):
                if self.__orientation == Qt.Vertical:
                    painter.drawLine(QPointF(-x, y), QPointF(x, y))
                else:
                    painter.drawLine(QPointF(y, -x), QPointF(y, x))

        painter.restore()

    def _create_violin(self, data: np.ndarray) -> QPainterPath:
        if self.__kde is None:
            x, p = np.zeros(1), np.zeros(1)
        else:
            x = np.linspace(data.min() - self.__bandwidth * 2,
                            data.max() + self.__bandwidth * 2, 1000)
            p = np.exp(self.__kde.score_samples(x.reshape(-1, 1)))

        if self.__orientation == Qt.Vertical:
            pts = [QPointF(pi, xi) for xi, pi in zip(x, p)]
            pts += [QPointF(-pi, xi) for xi, pi in reversed(list(zip(x, p)))]
        else:
            pts = [QPointF(xi, pi) for xi, pi in zip(x, p)]
            pts += [QPointF(xi, -pi) for xi, pi in reversed(list(zip(x, p)))]
        pts += pts[:1]

        polygon = QPolygonF(pts)
        path = QPainterPath()
        path.addPolygon(polygon)
        return path

    def _create_rug_plot(self, data: np.ndarray) -> Tuple:
        unique_data = np.unique(data)
        if self.__kde is None:
            return self.RugPlot(unique_data, np.zeros(unique_data.size))

        density = np.exp(self.__kde.score_samples(unique_data.reshape(-1, 1)))
        return self.RugPlot(unique_data, density)


class BoxItem(pg.GraphicsObject):
    Stats = namedtuple("Stats", "min q25 q75 max")

    def __init__(self, data: np.ndarray, rect: QRectF,
                 orientation: Qt.Orientations):
        self.__bounding_rect = rect
        self.__orientation = orientation

        self.__box_plot_data: BoxItem.Stats = self._create_box_plot(data)

        super().__init__()

    def boundingRect(self) -> QRectF:
        return self.__bounding_rect

    def paint(self, painter: QPainter, _, widget: QWidget):
        painter.save()

        q0, q25, q75, q100 = self.__box_plot_data
        if self.__orientation == Qt.Vertical:
            quartile1 = QPointF(0, q0), QPointF(0, q100)
            quartile2 = QPointF(0, q25), QPointF(0, q75)
        else:
            quartile1 = QPointF(q0, 0), QPointF(q100, 0)
            quartile2 = QPointF(q25, 0), QPointF(q75, 0)

        factor = widget.devicePixelRatio()
        painter.setPen(pg.mkPen(QColor(Qt.black), width=2 * factor))
        painter.drawLine(*quartile1)
        painter.setPen(pg.mkPen(QColor(Qt.black), width=6 * factor))
        painter.drawLine(*quartile2)

        painter.restore()

    @staticmethod
    def _create_box_plot(data: np.ndarray) -> Tuple:
        if data.size == 0:
            return BoxItem.Stats(*[0] * 4)

        q25, q75 = np.percentile(data, [25, 75])
        whisker_lim = 1.5 * stats.iqr(data)
        min_ = np.min(data[data >= (q25 - whisker_lim)])
        max_ = np.max(data[data <= (q75 + whisker_lim)])
        return BoxItem.Stats(min_, q25, q75, max_)


class MedianItem(pg.ScatterPlotItem):
    def __init__(self, data: np.ndarray, orientation: Qt.Orientations):
        self.__value = value = 0 if data.size == 0 else np.median(data)
        x, y = (0, value) if orientation == Qt.Vertical else (value, 0)
        super().__init__(x=[x], y=[y], size=4,
                         pen=pg.mkPen(QColor(Qt.white)),
                         brush=pg.mkBrush(QColor(Qt.white)))

    @property
    def value(self):
        return self.__value


class StripItem(pg.ScatterPlotItem):
    def __init__(self, data: np.ndarray, lim: float, color: QColor,
                 orientation: Qt.Orientations):
        x = np.random.RandomState(0).uniform(-lim, lim, data.size)
        x, y = (x, data) if orientation == Qt.Vertical else (data, x)
        color = color.lighter(150)
        super().__init__(x=x, y=y, size=5, brush=pg.mkBrush(color))


class ViolinPlot(pg.PlotWidget):
    selection_changed = Signal(list)

    def __init__(self, parent: OWWidget, kernel: str,
                 orientation: Qt.Orientations, show_box_plot: bool,
                 show_strip_plot: bool, show_rug_plot: bool, sort_items: bool):

        # data
        self.__values: np.ndarray = None
        self.__value_var: ContinuousVariable = None
        self.__group_values: Optional[np.ndarray] = None
        self.__group_var: Optional[DiscreteVariable] = None

        # settings
        self.__kernel = kernel
        self.__orientation = orientation
        self.__show_box_plot = show_box_plot
        self.__show_strip_plot = show_strip_plot
        self.__show_rug_plot = show_rug_plot
        self.__sort_items = sort_items

        # items
        self.__violin_items: List[ViolinItem] = []
        self.__box_items: List[BoxItem] = []
        self.__median_items: List[MedianItem] = []
        self.__strip_items: List[pg.ScatterPlotItem] = []

        # selection
        self.__selection: List[int] = []

        super().__init__(parent, viewBox=pg.ViewBox(),
                         background="w", enableMenu=False,
                         axisItems={"bottom": AxisItem("bottom"),
                                    "left": AxisItem("left")})
        self.setAntialiasing(True)
        self.hideButtons()
        self.getPlotItem().setContentsMargins(10, 10, 10, 10)
        self.setMouseEnabled(False, False)

        self.parameter_setter = ParameterSetter(self)

    @property
    def _item_width(self):
        if not self.__violin_items:
            return 0
        return max(item.violin_width for item in self.__violin_items) * 2.5

    def set_data(self, values: np.ndarray, value_var: ContinuousVariable,
                 group_values: Optional[np.ndarray],
                 group_var: Optional[DiscreteVariable]):
        self.__values = values
        self.__value_var = value_var
        self.__group_values = group_values
        self.__group_var = group_var
        self._set_axes()
        self._plot_data()

    def set_kernel(self, kernel: str):
        if self.__kernel != kernel:
            self.__kernel = kernel
            self._plot_data()

    def set_orientation(self, orientation: Qt.Orientations):
        if self.__orientation != orientation:
            self.__orientation = orientation
            self._clear_axes()
            self._set_axes()
            self._plot_data()

    def set_show_box_plot(self, show: bool):
        if self.__show_box_plot != show:
            self.__show_box_plot = show
            for item in self.__box_items:
                item.setVisible(show)
            for item in self.__median_items:
                item.setVisible(show)

    def set_show_strip_plot(self, show: bool):
        if self.__show_strip_plot != show:
            self.__show_strip_plot = show
            for item in self.__strip_items:
                item.setVisible(show)

    def set_show_rug_plot(self, show: bool):
        if self.__show_rug_plot != show:
            self.__show_rug_plot = show
            for item in self.__violin_items:
                item.set_show_rug_plot(show)

    def set_sort_items(self, sort_items: bool):
        if self.__sort_items != sort_items:
            self.__sort_items = sort_items
            if self.__group_var is not None:
                self.order_items()

    def order_items(self):
        assert self.__group_var is not None

        medians = [item.value for item in self.__median_items]
        indices = np.argsort(medians) if self.__sort_items \
            else range(len(medians))

        for i, index in enumerate(indices):
            violin: ViolinItem = self.__violin_items[index]
            box: BoxItem = self.__box_items[index]
            median: MedianItem = self.__median_items[index]
            strip: StripItem = self.__strip_items[index]

            if self.__orientation == Qt.Vertical:
                x = i * self._item_width
                violin.setX(x)
                box.setX(x)
                median.setX(x)
                strip.setX(x)
            else:
                y = - i * self._item_width
                violin.setY(y)
                box.setY(y)
                median.setY(y)
                strip.setY(y)

        sign = 1 if self.__orientation == Qt.Vertical else -1
        side = "bottom" if self.__orientation == Qt.Vertical else "left"
        ticks = [[(i * self._item_width * sign,
                   self.__group_var.values[index])
                  for i, index in enumerate(indices)]]
        self.getAxis(side).setTicks(ticks)

    def _set_axes(self):
        if self.__value_var is None:
            return
        value_title = self.__value_var.name
        group_title = self.__group_var.name if self.__group_var else ""
        vertical = self.__orientation == Qt.Vertical
        self.getAxis("left" if vertical else "bottom").setLabel(value_title)
        self.getAxis("bottom" if vertical else "left").setLabel(group_title)

    def _plot_data(self):
        self._clear_data_items()
        if self.__values is None:
            return

        if not self.__group_var:
            self._set_violin_item(self.__values, QColor(Qt.lightGray))
        else:
            assert self.__group_values is not None
            for index in range(len(self.__group_var.values)):
                mask = self.__group_values == index
                color = QColor(*self.__group_var.colors[index])
                self._set_violin_item(self.__values[mask], color)

            self.order_items()

    def _set_violin_item(self, values: np.ndarray, color: QColor):
        values = values[~np.isnan(values)]

        violin = ViolinItem(values, color, self.__kernel,
                            self.__show_rug_plot, self.__orientation)
        self.addItem(violin)
        self.__violin_items.append(violin)

        box = BoxItem(values, violin.boundingRect(), self.__orientation)
        box.setVisible(self.__show_box_plot)
        self.addItem(box)
        self.__box_items.append(box)

        median = MedianItem(values, self.__orientation)
        median.setVisible(self.__show_box_plot)
        self.addItem(median)
        self.__median_items.append(median)

        br = violin.boundingRect()
        lim = br.width() if self.__orientation == Qt.Vertical else br.height()
        strip = StripItem(values, lim / 2, color, self.__orientation)
        strip.setVisible(self.__show_strip_plot)
        self.addItem(strip)
        self.__strip_items.append(strip)

    def clear_plot(self):
        self.clear()
        self._clear_data()
        self._clear_data_items()
        self._clear_axes()
        self._clear_selection()

    def _clear_data(self):
        self.__values = None
        self.__value_var = None
        self.__group_values = None
        self.__group_var = None

    def _clear_data_items(self):
        for i in range(len(self.__violin_items)):
            self.removeItem(self.__violin_items[i])
            self.removeItem(self.__box_items[i])
            self.removeItem(self.__median_items[i])
            self.removeItem(self.__strip_items[i])
        self.__violin_items.clear()
        self.__box_items.clear()
        self.__median_items.clear()
        self.__strip_items.clear()

    def _clear_axes(self):
        self.setAxisItems({"bottom": AxisItem(orientation="bottom"),
                           "left": AxisItem(orientation="left")})
        Updater.update_axes_titles_font(
            self.parameter_setter.axis_items,
            **self.parameter_setter.titles_settings
        )
        Updater.update_axes_ticks_font(
            self.parameter_setter.axis_items,
            **self.parameter_setter.ticks_settings
        )

    def _clear_selection(self):
        self.__selection = []

    @staticmethod
    def sizeHint() -> QSize:
        return QSize(800, 600)


class OWViolinPlot(OWWidget):
    name = "Violin Plot"
    description = "Visualize the distribution of feature" \
                  " values in a violin plot."
    icon = "icons/ViolinPlot.svg"
    priority = 110
    keywords = ["kernel", "density"]

    class Inputs:
        data = Input("Data", Table)

    class Outputs:
        selected_data = Output("Selected Data", Table, default=True)
        annotated_data = Output(ANNOTATED_DATA_SIGNAL_NAME, Table)

    class Error(OWWidget.Error):
        no_cont_features = Msg("Plotting requires a numeric feature.")
        not_enough_instances = Msg("Plotting requires at least two instances.")

    KERNELS = ["gaussian", "epanechnikov", "linear"]
    KERNEL_LABELS = ["Normal kernel", "Epanechnikov kernel", "Linear kernel"]

    settingsHandler = DomainContextHandler()
    value_var = ContextSetting(None)
    order_by_importance = Setting(False)
    group_var = ContextSetting(None)
    order_grouping_by_importance = Setting(False)
    show_box_plot = Setting(True)
    show_strip_plot = Setting(False)
    show_rug_plot = Setting(False)
    order_violins = Setting(False)
    orientation_index = Setting(1)  # Vertical
    kernel_index = Setting(0)  # Normal kernel
    selection = Setting([], schema_only=True)
    visual_settings = Setting({}, schema_only=True)

    graph_name = "graph.plotItem"

    def __init__(self):
        super().__init__()
        self.data: Optional[Table] = None
        self.orig_data: Optional[Table] = None
        self.graph: ViolinPlot = None
        self._value_var_model: VariableListModel = None
        self._group_var_model: VariableListModel = None
        self._value_var_view: ListViewSearch = None
        self._group_var_view: ListViewSearch = None
        self._order_violins_cb: QCheckBox = None
        self.__pending_selection = self.selection

        self.setup_gui()
        VisualSettingsDialog(
            self, self.graph.parameter_setter.initial_settings
        )

    def setup_gui(self):
        self._add_graph()
        self._add_controls()

    def _add_graph(self):
        box = gui.vBox(self.mainArea)
        self.graph = ViolinPlot(self, self.kernel, self.orientation,
                                self.show_box_plot, self.show_strip_plot,
                                self.show_rug_plot, self.order_violins)
        self.graph.selection_changed.connect(self.__selection_changed)
        box.layout().addWidget(self.graph)

    def __selection_changed(self, indices: List):
        self.selection = list(set(self.grouped_indices[indices]))
        self.commit()

    def _add_controls(self):
        self._value_var_model = VariableListModel()
        sorted_model = SortProxyModel(sortRole=Qt.UserRole)
        sorted_model.setSourceModel(self._value_var_model)
        sorted_model.sort(0)

        view = self._value_var_view = ListViewSearch()
        view.setModel(sorted_model)
        view.setMinimumSize(QSize(30, 30))
        view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        view.selectionModel().selectionChanged.connect(
            self.__value_var_changed
        )

        self._group_var_model = VariableListModel(placeholder="None")
        sorted_model = SortProxyModel(sortRole=Qt.UserRole)
        sorted_model.setSourceModel(self._group_var_model)
        sorted_model.sort(0)

        view = self._group_var_view = ListViewSearch()
        view.setModel(sorted_model)
        view.setMinimumSize(QSize(30, 30))
        view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        view.selectionModel().selectionChanged.connect(
            self.__group_var_changed
        )

        box = gui.vBox(self.controlArea, "Variable")
        box.layout().addWidget(self._value_var_view)
        gui.checkBox(box, self, "order_by_importance",
                     "Order by relevance to subgroups",
                     tooltip="Order by 𝜒² or ANOVA over the subgroups",
                     callback=self.apply_value_var_sorting)

        box = gui.vBox(self.controlArea, "Subgroups")
        box.layout().addWidget(self._group_var_view)
        gui.checkBox(box, self, "order_grouping_by_importance",
                     "Order by relevance to variable",
                     tooltip="Order by 𝜒² or ANOVA over the variable values",
                     callback=self.apply_group_var_sorting)

        box = gui.vBox(self.controlArea, "Display",
                       sizePolicy=(QSizePolicy.Minimum, QSizePolicy.Maximum),
                       addSpace=False)
        gui.checkBox(box, self, "show_box_plot", "Box plot",
                     callback=self.__show_box_plot_changed)
        gui.checkBox(box, self, "show_strip_plot", "Strip plot",
                     callback=self.__show_strip_plot_changed)
        gui.checkBox(box, self, "show_rug_plot", "Rug plot",
                     callback=self.__show_rug_plot_changed)
        self._order_violins_cb = gui.checkBox(
            box, self, "order_violins", "Order subgroups",
            callback=self.__order_violins_changed,
        )
        gui.radioButtons(box, self, "orientation_index",
                         ["Horizontal", "Vertical"], label="Orientation: ",
                         orientation=Qt.Horizontal,
                         callback=self.__orientation_changed)

        box = gui.vBox(self.controlArea, "Density Estimation",
                       sizePolicy=(QSizePolicy.Minimum, QSizePolicy.Maximum),
                       addSpace=False)
        gui.comboBox(box, self, "kernel_index", items=self.KERNEL_LABELS,
                     callback=self.__kernel_changed)

        # stretch over buttonsArea
        self.left_side.layout().setStretch(0, 9999999)

        self._set_input_summary(None)
        self._set_output_summary(None)

    def __value_var_changed(self, selection: QItemSelection):
        if not selection:
            return
        self.value_var = selection.indexes()[0].data(gui.TableVariable)
        self.apply_group_var_sorting()
        self.setup_plot()

    def __group_var_changed(self, selection: QItemSelection):
        if not selection:
            return
        self.group_var = selection.indexes()[0].data(gui.TableVariable)
        self.apply_value_var_sorting()
        self.enable_order_violins_cb()
        self.setup_plot()

    def __show_box_plot_changed(self):
        self.graph.set_show_box_plot(self.show_box_plot)

    def __show_strip_plot_changed(self):
        self.graph.set_show_strip_plot(self.show_strip_plot)

    def __show_rug_plot_changed(self):
        self.graph.set_show_rug_plot(self.show_rug_plot)

    def __order_violins_changed(self):
        self.graph.set_sort_items(self.order_violins)

    def __orientation_changed(self):
        self.graph.set_orientation(self.orientation)

    def __kernel_changed(self):
        self.graph.set_kernel(self.kernel)

    @property
    def kernel(self):
        # pylint: disable=invalid-sequence-index
        return self.KERNELS[self.kernel_index]

    @property
    def orientation(self):
        return [Qt.Horizontal, Qt.Vertical][self.orientation_index]

    @Inputs.data
    @check_sql_input
    def set_data(self, data: Optional[Table]):
        self.closeContext()
        self.clear()
        self.orig_data = self.data = data
        self._set_input_summary(data)
        self.check_data()
        self.init_list_view()
        self.openContext(self.data)
        self.set_list_view_selection()
        self.apply_value_var_sorting()
        self.apply_group_var_sorting()
        self.enable_order_violins_cb()
        self.setup_plot()
        self.commit()

    def check_data(self):
        self.clear_messages()
        if self.data is not None:
            if self.data.domain.has_continuous_attributes(True, True) == 0:
                self.Error.no_cont_features()
                self.data = None
            elif len(self.data) < 2:
                self.Error.not_enough_instances()
                self.data = None

    def init_list_view(self):
        if not self.data:
            return

        domain = self.data.domain
        self._value_var_model[:] = [
            var for var in chain(
                domain.class_vars, domain.metas, domain.attributes)
            if var.is_continuous and not var.attributes.get("hidden", False)]
        self._group_var_model[:] = [None] + [
            var for var in chain(
                domain.class_vars, domain.metas, domain.attributes)
            if var.is_discrete and not var.attributes.get("hidden", False)]

        if len(self._value_var_model) > 0:
            self.value_var = self._value_var_model[0]

        self.group_var = self._group_var_model[0]
        if domain.class_var and domain.class_var.is_discrete:
            self.group_var = domain.class_var

    def set_list_view_selection(self):
        for view, var, callback in ((self._value_var_view, self.value_var,
                                     self.__value_var_changed),
                                    (self._group_var_view, self.group_var,
                                     self.__group_var_changed)):
            src_model = view.model().sourceModel()
            if var not in src_model:
                continue
            sel_model = view.selectionModel()
            sel_model.selectionChanged.disconnect(callback)
            row = src_model.indexOf(var)
            index = view.model().index(row, 0)
            sel_model.select(index, sel_model.ClearAndSelect)
            self._ensure_selection_visible(view)
            sel_model.selectionChanged.connect(callback)

    def apply_value_var_sorting(self):
        def compute_score(attr):
            if attr is group_var:
                return 3
            col = self.data.get_column_view(attr)[0].astype(float)
            groups = (col[group_col == i] for i in range(n_groups))
            groups = (col[~np.isnan(col)] for col in groups)
            groups = [group for group in groups if len(group)]
            p = stats.f_oneway(*groups)[1] if len(groups) > 1 else 2
            if np.isnan(p):
                return 2
            return p

        if self.data is None:
            return
        group_var = self.group_var
        if self.order_by_importance and group_var is not None:
            n_groups = len(group_var.values)
            group_col = self.data.get_column_view(group_var)[0].astype(float)
            self._sort_list(self._value_var_model, self._value_var_view,
                            compute_score)
        else:
            self._sort_list(self._value_var_model, self._value_var_view, None)

    def apply_group_var_sorting(self):
        def compute_stat(group):
            if group is value_var:
                return 3
            if group is None:
                return -1
            col = self.data.get_column_view(group)[0].astype(float)
            groups = (value_col[col == i] for i in range(len(group.values)))
            groups = (col[~np.isnan(col)] for col in groups)
            groups = [group for group in groups if len(group)]
            p = stats.f_oneway(*groups)[1] if len(groups) > 1 else 2
            if np.isnan(p):
                return 2
            return p

        if self.data is None:
            return
        value_var = self.value_var
        if self.order_grouping_by_importance:
            value_col = self.data.get_column_view(value_var)[0].astype(float)
            self._sort_list(self._group_var_model, self._group_var_view,
                            compute_stat)
        else:
            self._sort_list(self._group_var_model, self._group_var_view, None)

    def _sort_list(self, source_model, view, key=None):
        if key is None:
            c = count()

            def key(_):  # pylint: disable=function-redefined
                return next(c)

        for i, attr in enumerate(source_model):
            source_model.setData(source_model.index(i), key(attr), Qt.UserRole)
        self._ensure_selection_visible(view)

    @staticmethod
    def _ensure_selection_visible(view):
        selection = view.selectedIndexes()
        if len(selection) == 1:
            view.scrollTo(selection[0])

    def enable_order_violins_cb(self):
        self._order_violins_cb.setEnabled(self.group_var is not None)

    def setup_plot(self):
        self.graph.clear_plot()
        if not self.data:
            return

        y = self.data.get_column_view(self.value_var)[0].astype(float)
        x = None
        if self.group_var:
            x = self.data.get_column_view(self.group_var)[0].astype(float)
        self.graph.set_data(y, self.value_var, x, self.group_var)

    def commit(self):
        selected = None
        if self.data is not None and bool(self.selection):
            selected = self.data[self.selection]
        annotated = create_annotated_table(self.orig_data, self.selection)
        self._set_output_summary(selected)
        self.Outputs.selected_data.send(selected)
        self.Outputs.annotated_data.send(annotated)

    def clear(self):
        self._value_var_model[:] = []
        self._group_var_model[:] = []
        self.selection = None
        self.graph.clear_plot()

    def _set_input_summary(self, data: Optional[Table]):
        self._set_summary(data, self.info.NoInput, self.info.set_input_summary)

    def _set_output_summary(self, data: Optional[Table]):
        self._set_summary(data, self.info.NoOutput,
                          self.info.set_output_summary)

    @staticmethod
    def _set_summary(data, empty, setter):
        summary = len(data) if data else empty
        details = format_summary_details(data) if data else ""
        setter(summary, details)

    def send_report(self):
        if self.data is None:
            return
        self.report_plot()

    def set_visual_settings(self, key: KeyType, value: ValueType):
        self.graph.parameter_setter.set_parameter(key, value)
        self.visual_settings[key] = value
    #
    # def showEvent(self, event):
    #     super().showEvent(event)
    #     self.graph.reset_view()


if __name__ == "__main__":
    from Orange.widgets.utils.widgetpreview import WidgetPreview

    WidgetPreview(OWViolinPlot).run(set_data=Table("heart_disease"))
