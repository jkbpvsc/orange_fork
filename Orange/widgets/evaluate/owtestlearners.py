from collections import OrderedDict, namedtuple
import functools

import numpy

from PyQt4 import QtGui
from PyQt4.QtGui import QTreeView, QStandardItemModel, QStandardItem
from PyQt4.QtCore import Qt

import Orange.data
import Orange.classification

from Orange.evaluation import testing, scoring

from Orange.widgets import widget, gui, settings


Input = namedtuple("Input", ["learner", "results", "stats"])


def test_statistics(results):
    return (scoring.CA(results),
            scoring.F1(results),
            scoring.Precision(results),
            scoring.Recall(results))


class OWTestLearners(widget.OWWidget):
    name = "Test Learners"
    description = ""
    icon = "icons/Experiment.svg"

    inputs = [("Learner", Orange.classification.Fitter,
               "set_learner", widget.Multiple),
              ("Data", Orange.data.Table, "set_train_data", widget.Default),
              ("Test Data", Orange.data.Table, "set_test_data")]

    outputs = [("Evaluation Results", testing.Results)]

    #: Resampling/testing types
    KFold, LeaveOneOut, Bootstrap, TestOnTrain, TestOnTest = 0, 1, 2, 3, 4

    #: Selected resampling type
    resampling = settings.Setting(0)
    #: Number of folds for K-fold cross validation
    k_folds = settings.Setting(10)
    #: Number of repeats for bootstrap sampling
    n_repeat = settings.Setting(10)
    #: Bootstrap sampling p
    sample_p = settings.Setting(75)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.train_data = None
        self.test_data = None

        #: An Ordered dictionary with current inputs and their testing
        #: results.
        self.learners = OrderedDict()

        sbox = gui.widgetBox(self.controlArea, "Sampling")
        rbox = gui.radioButtons(
            sbox, self, "resampling", callback=self._param_changed
        )
        gui.appendRadioButton(rbox, "Cross validation")
        ibox = gui.indentedBox(rbox)
        gui.spin(ibox, self, "k_folds", 2, 50, label="Number of folds:",
                 callback=self._param_changed)
        gui.appendRadioButton(rbox, "Leave one out")
        gui.appendRadioButton(rbox, "Random sampling")
        ibox = gui.indentedBox(rbox)
        gui.spin(ibox, self, "n_repeat", 2, 50, label="Repeat train/test",
                 callback=self._param_changed)
        gui.widgetLabel(ibox, "Relative training set size:")
        gui.hSlider(ibox, self, "sample_p", minValue=1, maxValue=100,
                    ticks=20, vertical=False,
                    callback=self._param_changed)

        gui.appendRadioButton(rbox, "Test on train data")
        gui.appendRadioButton(rbox, "Test on test data")

        rbox.layout().addSpacing(5)
        gui.button(rbox, self, "Apply", callback=self.apply)

        gui.rubber(self.controlArea)

        self.view = QTreeView(
            rootIsDecorated=False,
            uniformRowHeights=True,
            wordWrap=True,
            editTriggers=QTreeView.NoEditTriggers
        )

        self.result_model = QStandardItemModel()
        self.result_model.setHorizontalHeaderLabels(
            ["Method", "CA", "F1", "Precision", "Recall"]
        )
        self.view.setModel(self.result_model)
        box = gui.widgetBox(self.mainArea, "Evaluation Results")
        box.layout().addWidget(self.view)

    def set_learner(self, learner, key):
        if key in self.learners and learner is None:
            del self.learners[key]
        else:
            self.learners[key] = Input(learner, None, None)

    def set_train_data(self, data):
        self.train_data = data
        self._invalidate()

    def set_test_data(self, data):
        self.test_data = data
        if self.resampling == OWTestLearners.TestOnTest:
            self._invalidate()

    def handleNewSignals(self):
        self.update_results()
        self.commit()

    def _param_changed(self):
        self._invalidate()

    def update_results(self):
        # items in need of an update
        items = [(key, input) for key, input in self.learners.items()
                 if input.results is None]
        learners = [input.learner for _, input in items]

        self.setStatusMessage("Running")

        # TODO: Test each learner individually

        if self.resampling == OWTestLearners.KFold:
            results = testing.CrossValidation(
                self.train_data, learners, k=self.k_folds
            )
        elif self.resampling == OWTestLearners.LeaveOneOut:
            results = testing.LeaveOneOut(self.train_data, learners)
        elif self.resampling == OWTestLearners.Bootstrap:
            p = self.sample_p / 100.0
            results = testing.Bootstrap(
                self.train_data, learners, n_resamples=self.n_repeat, p=p
            )
        elif self.resampling == OWTestLearners.TestOnTrain:
            results = testing.TestOnTrainingData(self.train_data, learners)
        elif self.resampling == OWTestLearners.TestOnTest:
            assert self.test_data is not None
            results = testing.TestOnTestData(
                self.train_data, self.test_data, learners
            )
        else:
            assert False

        results = list(split_by_model(results))
        stats = [test_statistics(res) for res in results]
        for (key, input), res, stat in zip(items, results, stats):
            self.learners[key] = input._replace(results=res, stats=stat)

        self.setStatusMessage("")
        self._update_stats_model()

    def _update_stats_model(self):
        model = self.view.model()

        for r in reversed(range(model.rowCount())):
            model.takeRow(r)

        for input in self.learners.values():
            name = learner_name(input.learner)
            row = []
            head = QStandardItem()
            head.setData(name, Qt.DisplayRole)
            row.append(head)
            for stat in input.stats:
                item = QStandardItem()
                item.setData(float(stat[0]), Qt.DisplayRole)
                row.append(item)
            model.appendRow(row)

        self.view.resizeColumnToContents(0)

    def _invalidate(self, which=None):
        if which is None:
            which = self.learners.keys()

        all_keys = list(self.learners.keys())
        model = self.view.model()

        for key in which:
            self.learners[key] = \
                self.learners[key]._replace(results=None, stats=None)

            if key in self.learners:
                row = all_keys.index(key)
                for c in range(1, model.columnCount()):
                    item = model.item(row, c)
                    item.setData(None, Qt.DisplayRole)

    def apply(self):
        self.update_results()
        self.commit()

    def commit(self):
        results = [val.results for val in self.learners.values()]
        if results:
            combined = results_merge(results)
        else:
            combined = None
        self.send("Evaluation Results", combined)


def learner_name(learner):
    return getattr(learner, "name", type(learner).__name__)


def split_by_model(results):
    """
    Split evaluation results by models
    """
    data = results.data
    nmethods = len(results.predicted)
    for i in range(nmethods):
        res = testing.Results()
        res.data = data
        res.row_indices = results.row_indices
        res.actual = results.actual
        res.predicted = results.predicted[(i,), :]
        res.probabilities = results.probabilities[(i,), :, :]

        if results.models:
            res.models = [mf[i] for mf in results.models]

        if results.folds:
            res.folds = results.folds

        yield res


def results_add_by_model(x, y):
    def is_empty(res):
        return (getattr(res, "models", None) is None
                and getattr(res, "row_indices", None) is None)

    if is_empty(x):
        return y
    elif is_empty(y):
        return x

    assert (x.row_indices == y.row_indices).all()
    assert (x.actual == y.actual).all()

    res = testing.Results()
    res.data = x.data
    res.row_indices = x.row_indices
    res.folds = x.folds
    res.actual = x.actual
    res.predicted = numpy.vstack((x.predicted, y.predicted))
    res.probabilities = numpy.vstack((x.probabilities, y.probabilities))

    if x.models is not None:
        res.models = [xm + ym for xm, ym in zip(x.models, y.models)]
    return res


def results_merge(results):
    return functools.reduce(results_add_by_model, results, testing.Results())


def main():
    from Orange.classification import logistic_regression as lr, naive_bayes as nb

    app = QtGui.QApplication([])
    data = Orange.data.Table("iris")
    w = OWTestLearners()
    w.show()
    w.set_train_data(data)
    w.set_test_data(data)
    w.set_learner(lr.LogisticRegressionLearner(), 1)
    w.set_learner(nb.BayesLearner(), 2)
    w.handleNewSignals()
    return app.exec_()

if __name__ == "__main__":
    import sys
    sys.exit(main())
