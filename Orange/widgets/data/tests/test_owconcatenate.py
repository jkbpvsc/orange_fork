# Test methods with long descriptive names can omit docstrings
# pylint: disable=missing-docstring

import numpy as np

from Orange.data import Table, Domain
from Orange.widgets.data.owconcatenate import OWConcatenate
from Orange.widgets.tests.base import WidgetTest


class TestOWConcatenate(WidgetTest):

    def setUp(self):
        self.widget = self.create_widget(OWConcatenate)
        self.iris = Table("iris")
        self.titanic = Table("titanic")

    def test_single_input(self):
        self.assertIsNone(self.get_output("Data"))
        self.send_signal("Primary Data", self.iris)
        output = self.get_output("Data")
        self.assertEqual(list(output), list(self.iris))
        self.send_signal("Primary Data", None)
        self.assertIsNone(self.get_output("Data"))
        self.send_signal("Additional Data", self.iris)
        output = self.get_output("Data")
        self.assertEqual(list(output), list(self.iris))
        self.send_signal("Additional Data", None)
        self.assertIsNone(self.get_output("Data"))

    def test_two_inputs_union(self):
        self.send_signal("Additional Data", self.iris, 0)
        self.send_signal("Additional Data", self.titanic, 1)
        output = self.get_output("Data")
        # needs to contain all instances
        self.assertEqual(len(output), len(self.iris) + len(self.titanic))
        # needs to contain all variables
        outvars = output.domain.variables
        self.assertLess(set(self.iris.domain.variables), set(outvars))
        self.assertLess(set(self.titanic.domain.variables), set(outvars))
        # the first part of the data set is iris, the second part is titanic
        np.testing.assert_equal(self.iris.X, output.X[:len(self.iris), :-3])
        self.assertTrue(np.isnan(output.X[:len(self.iris), -3:]).all())
        np.testing.assert_equal(self.titanic.X, output.X[len(self.iris):, -3:])
        self.assertTrue(np.isnan(output.X[len(self.iris):, :-3]).all())

    def test_two_inputs_intersection(self):
        self.send_signal("Additional Data", self.iris, 0)
        self.send_signal("Additional Data", self.titanic, 1)
        self.widget.controls.merge_type.buttons[1].click()
        output = self.get_output("Data")
        # needs to contain all instances
        self.assertEqual(len(output), len(self.iris) + len(self.titanic))
        # no common variables
        outvars = output.domain.variables
        self.assertEqual(0, len(outvars))

    def test_source(self):
        self.send_signal("Additional Data", self.iris, 0)
        self.send_signal("Additional Data", self.titanic, 1)
        outputb = self.get_output("Data")
        outvarsb = outputb.domain.variables
        def get_source():
            output = self.get_output("Data")
            outvars = output.domain.variables + output.domain.metas
            return (set(outvars) - set(outvarsb)).pop()
        # test adding source
        self.widget.controls.append_source_column.toggle()
        source = get_source()
        self.assertEquals(source.name, "Source ID")
        # test name changing
        self.widget.controls.source_attr_name.setText("Source")
        self.widget.controls.source_attr_name.callback()
        source = get_source()
        self.assertEquals(source.name, "Source")
        # test source_column role
        places = ["class_vars", "attributes", "metas"]
        for i, place in enumerate(places):
            self.widget.source_column_role = i
            self.widget.apply()
            source = get_source()
            output = self.get_output("Data")
            self.assertTrue(source in getattr(output.domain, place))
            data = Table(Domain([source]), output)
            np.testing.assert_equal(data[:len(self.iris)].X, 0)
            np.testing.assert_equal(data[len(self.iris):].X, 1)

    def test_singleclass_source_class(self):
        self.send_signal("Primary Data", self.iris)
        # add source into a class variable
        self.widget.controls.append_source_column.toggle()

    def test_disable_merging_on_primary(self):
        self.assertTrue(self.widget.mergebox.isEnabled())
        self.send_signal("Primary Data", self.iris)
        self.assertFalse(self.widget.mergebox.isEnabled())
        self.send_signal("Primary Data", None)
        self.assertTrue(self.widget.mergebox.isEnabled())
