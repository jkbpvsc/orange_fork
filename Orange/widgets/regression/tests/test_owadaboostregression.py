# Test methods with long descriptive names can omit docstrings
# pylint: disable=missing-docstring
from Orange.regression import TreeRegressionLearner, KNNRegressionLearner
from Orange.widgets.regression.owadaboostregression import OWAdaBoostRegression
from Orange.widgets.tests.base import (WidgetTest, WidgetLearnerTestMixin,
                                       GuiToParam)


class TestOWAdaBoostRegression(WidgetTest, WidgetLearnerTestMixin):
    def setUp(self):
        self.widget = self.create_widget(OWAdaBoostRegression,
                                         stored_settings={"auto_apply": False})
        self.init()

        def combo_set_value(i, x):
            x.activated.emit(i)
            x.setCurrentIndex(i)

        losses = [loss.lower() for loss in self.widget.losses]
        nest_spin = self.widget.n_estimators_spin
        nest_min_max = [nest_spin.minimum(), nest_spin.maximum()]
        rate_spin = self.widget.learning_rate_spin
        rate_min_max = [rate_spin.minimum(), rate_spin.maximum()]
        self.gui_to_params = [
            GuiToParam('loss', self.widget.loss_combo,
                       lambda x: x.currentText().lower(),
                       combo_set_value, losses, list(range(len(losses)))),
            GuiToParam('learning_rate', rate_spin, lambda x: x.value(),
                       lambda i, x: x.setValue(i), rate_min_max, rate_min_max),
            GuiToParam('n_estimators', nest_spin, lambda x: x.value(),
                       lambda i, x: x.setValue(i), nest_min_max, nest_min_max)]

    def test_input_learner(self):
        """Check if base learner properly changes with learner on the input"""
        max_depth = 2
        default_base_est = self.widget.base_estimator
        self.assertIsInstance(default_base_est, TreeRegressionLearner)
        self.assertIsNone(default_base_est.params.get("max_depth"))
        self.send_signal("Learner", TreeRegressionLearner(max_depth=max_depth))
        self.assertEqual(self.widget.base_estimator.params.get("max_depth"),
                         max_depth)
        self.widget.apply_button.button.click()
        output_base_est = self.get_output("Learner").params.get("base_estimator")
        self.assertEqual(output_base_est.max_depth, max_depth)

    def test_input_learner_disconnect(self):
        """Check base learner after disconnecting learner on the input"""
        self.send_signal("Learner", KNNRegressionLearner())
        self.assertIsInstance(self.widget.base_estimator, KNNRegressionLearner)
        self.send_signal("Learner", None)
        self.assertEqual(self.widget.base_estimator,
                         self.widget.DEFAULT_BASE_ESTIMATOR)
