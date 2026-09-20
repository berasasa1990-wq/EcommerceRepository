from unittest.mock import Mock, patch

from django.db import transaction
from django.test import SimpleTestCase, TestCase

from .emails import _send_order_emails_in_background, queue_order_emails


class CheckoutNotificationTransactionTests(TestCase):
    def test_rollback_does_not_send_confirmation(self):
        with patch('EcommerceApp.emails.Thread') as thread:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(ValueError):
                    with transaction.atomic():
                        queue_order_emails(Mock(pk=123))
                        raise ValueError('Order failed')
            thread.assert_not_called()

    def test_thread_start_failure_does_not_fail_saved_order_response(self):
        with patch('EcommerceApp.emails.Thread', side_effect=RuntimeError('Unavailable')), \
             self.assertLogs('EcommerceApp.emails', level='ERROR'):
            with self.captureOnCommitCallbacks(execute=True):
                queue_order_emails(Mock(pk=123))


class CheckoutEmailWorkerTests(SimpleTestCase):
    def test_worker_loads_saved_order_and_closes_its_connections(self):
        with patch('EcommerceApp.emails.Order.objects.get') as get_order, \
             patch('EcommerceApp.emails.send_order_emails') as send, \
             patch('EcommerceApp.emails.close_old_connections') as prepare, \
             patch('EcommerceApp.emails.connections.close_all') as close:
            _send_order_emails_in_background(123)
            get_order.assert_called_once_with(pk=123)
            send.assert_called_once_with(get_order.return_value)
            prepare.assert_called_once()
            close.assert_called_once()

    def test_smtp_failure_is_logged_and_connections_closed(self):
        with patch('EcommerceApp.emails.Order.objects.get'), \
             patch('EcommerceApp.emails.send_order_emails', side_effect=TimeoutError('SMTP')), \
             patch('EcommerceApp.emails.close_old_connections'), \
             patch('EcommerceApp.emails.connections.close_all') as close, \
             self.assertLogs('EcommerceApp.emails', level='ERROR'):
            _send_order_emails_in_background(123)
            close.assert_called_once()
