"""
Unit tests for the interrupt poller's energy tracking stream wrapper.
Asserts exact RMS values on synthetic buffers per TASK-025 specification.
"""

import math
import struct
import unittest
from unittest.mock import MagicMock

import jarvis_voice


class EnergyTrackingStreamTests(unittest.TestCase):
    def test_zero_buffer_rms_is_exact_zero(self):
        """A buffer of all-zero samples has exact RMS of 0."""
        zero_bytes = b"\x00" * 2048
        mock_stream = MagicMock()
        mock_stream.read.return_value = zero_bytes

        tracker = jarvis_voice._EnergyTrackingStream(mock_stream, sample_width=2)
        data = tracker.read(1024)

        self.assertEqual(data, zero_bytes)
        self.assertEqual(tracker.peak_energy, 0)

    def test_constant_amplitude_rms_equals_exact_amplitude(self):
        """A buffer of constant amplitude has RMS equal to that amplitude."""
        amplitude = 750
        num_samples = 256
        constant_samples = [amplitude] * num_samples
        constant_bytes = struct.pack(f"<{num_samples}h", *constant_samples)

        mock_stream = MagicMock()
        mock_stream.read.return_value = constant_bytes

        tracker = jarvis_voice._EnergyTrackingStream(mock_stream, sample_width=2)
        data = tracker.read(num_samples)

        self.assertEqual(data, constant_bytes)
        self.assertEqual(tracker.peak_energy, amplitude)

        # Negative constant amplitude also has RMS equal to positive amplitude
        neg_samples = [-amplitude] * num_samples
        neg_bytes = struct.pack(f"<{num_samples}h", *neg_samples)
        mock_stream.read.return_value = neg_bytes

        tracker.reset_peak()
        self.assertEqual(tracker.peak_energy, 0)
        tracker.read(num_samples)
        self.assertEqual(tracker.peak_energy, amplitude)

    def test_peak_energy_tracks_maximum_across_multiple_reads(self):
        """Peak energy retains the maximum RMS observed across successive buffers."""
        amp1, amp2, amp3 = 150, 850, 400
        n = 100
        b1 = struct.pack(f"<{n}h", *([amp1] * n))
        b2 = struct.pack(f"<{n}h", *([amp2] * n))
        b3 = struct.pack(f"<{n}h", *([amp3] * n))

        mock_stream = MagicMock()
        mock_stream.read.side_effect = [b1, b2, b3]

        tracker = jarvis_voice._EnergyTrackingStream(mock_stream, sample_width=2)

        tracker.read(n)
        self.assertEqual(tracker.peak_energy, 150)

        tracker.read(n)
        self.assertEqual(tracker.peak_energy, 850)

        tracker.read(n)
        self.assertEqual(tracker.peak_energy, 850)

    def test_delegated_read_returns_bytes_unchanged(self):
        """Wrapper delegates read() to the underlying stream and returns identical bytes."""
        payload = bytes(range(256)) * 4
        mock_stream = MagicMock()
        mock_stream.read.return_value = payload

        tracker = jarvis_voice._EnergyTrackingStream(mock_stream, sample_width=2)
        result = tracker.read(len(payload))

        self.assertEqual(result, payload)
        self.assertIs(result, payload)

    def test_delegation_of_attributes_and_methods(self):
        """Wrapper delegates close() and arbitrary attributes to the underlying stream."""
        mock_stream = MagicMock()
        mock_stream.custom_attribute = "stream_property_value"
        mock_stream.is_active.return_value = True

        tracker = jarvis_voice._EnergyTrackingStream(mock_stream, sample_width=2)

        self.assertEqual(tracker.custom_attribute, "stream_property_value")
        self.assertTrue(tracker.is_active())

        tracker.close()
        mock_stream.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
