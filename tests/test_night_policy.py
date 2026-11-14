import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('driver',Path(__file__).resolve().parents[1]/'ops/night_driver.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)

class DriverPolicy(unittest.TestCase):
    def state(self):
        return {'principal':['a','b'],'initial_reset_at':200,'initial_wait_finished':False,'resets_used':0}
    def test_first_exhausted_window_waits_without_spending(self):
        s=self.state()
        with patch.object(d.Path,'read_text',return_value=json.dumps({'tokens':{'access_token':'placeholder'}})),patch.object(d,'principal',return_value=('a','b')),patch.object(d.time,'time',return_value=100),patch.object(d,'hermes_call',return_value={'banked':3,'rate':{'primary_window':{'used_percent':100}}}) as api:
            self.assertFalse(d.quota_gate(s));self.assertEqual(s['status'],'waiting_first_natural_reset');api.assert_called_once_with('read')
    def test_account_change_never_queries_or_resets(self):
        s=self.state()
        with patch.object(d.Path,'read_text',return_value=json.dumps({'tokens':{'access_token':'placeholder'}})),patch.object(d,'principal',return_value=('other','b')),patch.object(d,'hermes_call') as api:
            self.assertFalse(d.quota_gate(s));self.assertEqual(s['status'],'blocked_account_changed');api.assert_not_called()
    def test_exhausted_later_window_requires_reset_readback(self):
        s=self.state();s['initial_wait_finished']=True
        initial={'banked':3,'rate':{'primary_window':{'used_percent':100}}}
        result={'redemption':{'status':'reset'},'readback':{'banked':2,'rate':{'limit_reached':False}}}
        with patch.object(d.Path,'read_text',return_value=json.dumps({'tokens':{'access_token':'placeholder'}})),patch.object(d,'principal',return_value=('a','b')),patch.object(d.time,'time',return_value=300),patch.object(d,'save'),patch.object(d,'hermes_call',side_effect=[initial,result]) as api:
            self.assertTrue(d.quota_gate(s));self.assertEqual(s['resets_used'],1);self.assertEqual([c.args[0] for c in api.call_args_list],['read','reset'])
    def test_three_reset_budget_never_exceeded(self):
        s=self.state();s['resets_used']=3;s['initial_wait_finished']=True
        with patch.object(d.Path,'read_text',return_value=json.dumps({'tokens':{'access_token':'placeholder'}})),patch.object(d,'principal',return_value=('a','b')),patch.object(d,'hermes_call',return_value={'banked':4,'rate':{'primary_window':{'used_percent':100}}}) as api:
            self.assertFalse(d.quota_gate(s));api.assert_called_once_with('read')

if __name__=='__main__': unittest.main()
