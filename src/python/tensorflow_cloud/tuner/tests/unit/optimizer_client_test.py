# Lint as: python3
# Copyright 2020 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for optimizer_client."""

from googleapiclient import errors
from googleapiclient import http as googleapiclient_http
import httplib2
import mock
import tensorflow as tf
from tensorflow_cloud import version
from tensorflow_cloud.tuner.tuner import optimizer_client
from tensorflow_cloud.utils import google_api_client


class OptimizerClientTest(tf.test.TestCase):
    def setUp(self):
        super(OptimizerClientTest, self).setUp()
        self.addCleanup(mock.patch.stopall)

        self._study_id = "study-a"
        self._region = "us-central1"
        self._project_id = "project-a"
        self._trial_parent = "projects/{}/locations/{}/studies/{}".format(
            self._project_id, self._region, self._study_id
        )
        self._trial_name = "{}/trials/{}".format(self._trial_parent, "1")

        self._mock_discovery = mock.MagicMock()

        self._study_config = {
            "algorithm": "ALGORITHM_UNSPECIFIED",
            "metrics": [{"metric": "val_acc", "goal": "MAXIMIZE"}],
            "parameters": [
                {
                    "parameter": "learning_rate",
                    "discrete_value_spec": {"values": [0.0001, 0.001, 0.01]},
                    "type": "DISCRETE",
                }
            ],
        }

        self._client = optimizer_client._OptimizerClient(
            service_client=self._mock_discovery,
            project_id=self._project_id,
            region=self._region,
            study_id=self._study_id,
        )

    @mock.patch.object(optimizer_client, "discovery")
    def test_create_or_load_study_newstudy(self, mock_discovery):
        mock_create_study = mock.MagicMock()
        mock_discovery.build_from_document.return_value.projects().locations().studies().create = (
            mock_create_study
        )

        client = optimizer_client.create_or_load_study(
            project_id=self._project_id,
            region=self._region,
            study_id=self._study_id,
            study_config=self._study_config,
        )

        self.assertIsInstance(client, optimizer_client._OptimizerClient)

        _, mock_kwargs = mock_discovery.build_from_document.call_args
        self.assertIn("service", mock_kwargs)
        self.assertIsInstance(mock_kwargs["service"], dict)
        self.assertEqual(
            mock_kwargs["service"]["rootUrl"],
            # Regional endpoint must be specified for Optimizer client.
            "https://us-central1-ml.googleapis.com/",
        )

        mock_create_study.assert_called_with(
            body={"study_config": self._study_config},
            parent="projects/{}/locations/{}".format(self._project_id, self._region),
            studyId=self._study_id,
        )

    @mock.patch.object(optimizer_client, "discovery")
    def test_create_or_load_study_with_409_raises_RuntimeError(self, mock_discovery):
        """Verify that get_study gracefully handles 409 errors."""
        mock_request = mock.MagicMock()
        mock_request.execute.side_effect = errors.HttpError(
            httplib2.Response(info={"status": 409}), b""
        )
        mock_create_study = mock.MagicMock()
        mock_create_study.return_value = mock_request
        mock_discovery.build_from_document.return_value.projects().locations().studies().create = (
            mock_create_study
        )

        mock_get_study = mock.MagicMock()
        mock_get_study.side_effect = [
            errors.HttpError(httplib2.Response(info={"status": 400}), b"")
        ] * 3
        mock_discovery.build_from_document.return_value.projects().locations().studies().get = (
            mock_get_study
        )

        with self.assertRaisesRegex(
            RuntimeError,
            'GetStudy wasn\'t successful after 3 tries: <HttpError 400 "Ok">',
        ):
            optimizer_client.create_or_load_study(
                project_id=self._project_id,
                region=self._region,
                study_id=self._study_id,
                study_config=self._study_config,
            )

    @mock.patch.object(optimizer_client, "discovery")
    def test_create_or_load_study_with_409_success(self, mock_discovery):
        """Verify that get_study gracefully handles 409 errors."""
        mock_create_request = mock.MagicMock()
        mock_create_request.execute.side_effect = errors.HttpError(
            httplib2.Response(info={"status": 409}), b""
        )
        mock_create_study = mock.MagicMock()
        mock_create_study.return_value = mock_create_request
        mock_discovery.build_from_document.return_value.projects().locations().studies().create = (
            mock_create_study
        )

        mock_get_request = mock.MagicMock()
        mock_get_request.execute.side_effect = [
            errors.HttpError(httplib2.Response(info={"status": 400}), b""),
            errors.HttpError(httplib2.Response(info={"status": 400}), b""),
            mock.DEFAULT,
        ]
        mock_get_study = mock.MagicMock()
        mock_get_study.side_effect = mock_get_request
        mock_discovery.build_from_document.return_value.projects().locations().studies().get = (
            mock_get_study
        )

        client = optimizer_client.create_or_load_study(
            project_id=self._project_id,
            region=self._region,
            study_id=self._study_id,
            study_config=self._study_config,
        )
        self.assertIsInstance(client, optimizer_client._OptimizerClient)

    def test_get_suggestions(self):
        mock_suggest = mock.MagicMock()
        self._mock_discovery.projects().locations().studies().trials().suggest = (
            mock_suggest
        )

        expected_response = {
            "trials": [
                {
                    "name": "1",
                    "state": "ACTIVE",
                    "parameters": [{"parameter": "learning_rate", "floatValue": 0.001}],
                }
            ]
        }
        mock_suggest_lro = mock.MagicMock()
        mock_suggest_lro.return_value.execute.return_value = {
            "name": "op_name",
            "done": True,
            "response": expected_response,
        }
        self._mock_discovery.projects().locations().operations().get = mock_suggest_lro

        suggestions = self._client.get_suggestions("tuner_0")
        mock_suggest.assert_called_once_with(
            parent=self._trial_parent,
            body={"client_id": "tuner_0", "suggestion_count": 1},
        )
        self.assertEqual(suggestions, expected_response)

    def test_get_suggestions_with_429(self):
        """Verify that get_suggestion gracefully handles 429 errors."""
        mock_request = mock.MagicMock()
        mock_request.execute.side_effect = errors.HttpError(
            httplib2.Response(info={"status": 429}), b""
        )
        mock_suggest = mock.MagicMock()
        mock_suggest.return_value = mock_request
        self._mock_discovery.projects().locations().studies().trials().suggest = (
            mock_suggest
        )

        suggestions = self._client.get_suggestions("tuner_0")
        self.assertEqual(suggestions, {})

    def test_report_intermediate_objective_value(self):
        mock_add_measurement = mock.MagicMock()
        self._mock_discovery.projects().locations().studies().trials().addMeasurement = (
            mock_add_measurement
        )

        self._client.report_intermediate_objective_value(
            step=1, elapsed_secs=2, metric_list=[{"val_acc": 0.8}], trial_id="1"
        )

        expected_measurement = {
            "stepCount": 1,
            "elapsedTime": {"seconds": 2},
            "metrics": [{"val_acc": 0.8}],
        }
        mock_add_measurement.assert_called_once_with(
            name=self._trial_name, body={"measurement": expected_measurement}
        )

    def test_should_trial_stop(self):
        mock_early_stop = mock.MagicMock()
        mock_early_stop.return_value.execute.return_value = {"name": "op_name"}
        self._mock_discovery.projects().locations().studies().trials().checkEarlyStoppingState = (
            mock_early_stop
        )

        mock_early_stop_lro = mock.MagicMock()
        mock_early_stop_lro.return_value.execute.return_value = {
            "name": "op_name",
            "done": True,
            "response": {"shouldStop": True},
        }
        self._mock_discovery.projects().locations().operations().get = (
            mock_early_stop_lro
        )

        mock_stop_trial = mock.MagicMock()
        self._mock_discovery.projects().locations().studies().trials().stop = (
            mock_stop_trial
        )

        actual_should_stop = self._client.should_trial_stop("1")

        self.assertEqual(actual_should_stop, True)
        mock_early_stop.assert_called_once_with(name=self._trial_name)
        mock_stop_trial.assert_called_once_with(name=self._trial_name)

    def test_complete_trial(self):
        mock_complete_trial = mock.MagicMock()
        expected_trial = {
            "name": "1",
            "state": "ACTIVE",
            "parameters": [{"parameter": "learning_rate", "floatValue": 0.001}],
            "finalMeasurement": {
                "stepCount": 3,
                "metrics": [{"metric": "val_acc", "value": 0.9}],
            },
            "trial_infeasible": False,
            "infeasible_reason": None,
        }
        mock_complete_trial.return_value.execute.return_value = expected_trial

        self._mock_discovery.projects().locations().studies().trials().complete = (
            mock_complete_trial
        )

        trial = self._client.complete_trial(trial_id="1", trial_infeasible=False)

        mock_complete_trial.assert_called_once_with(
            name=self._trial_name,
            body={"trial_infeasible": False, "infeasible_reason": None},
        )
        self.assertEqual(trial, expected_trial)

    def test_list_trials(self):
        mock_list_trials = mock.MagicMock()
        expected_trials = {
            "trials": [
                {
                    "name": "1",
                    "state": "COMPLETED",
                    "parameters": [{"parameter": "learning_rate", "floatValue": 0.01}],
                    "finalMeasurement": {
                        "stepCount": 3,
                        "metrics": [{"metric": "val_acc", "value": 0.7}],
                    },
                    "trial_infeasible": False,
                    "infeasible_reason": None,
                },
                {
                    "name": "2",
                    "state": "COMPLETED",
                    "parameters": [{"parameter": "learning_rate", "floatValue": 0.001}],
                    "finalMeasurement": {
                        "stepCount": 3,
                        "metrics": [{"metric": "val_acc", "value": 0.9}],
                    },
                    "trial_infeasible": False,
                    "infeasible_reason": None,
                },
            ]
        }
        mock_list_trials.return_value.execute.return_value = expected_trials
        self._mock_discovery.projects().locations().studies().trials().list = (
            mock_list_trials
        )

        trials = self._client.list_trials()

        mock_list_trials.assert_called_once_with(parent=self._trial_parent)
        self.assertEqual(len(trials), 2)

    def test_cloud_tuner_request_header(self):
        http_request = google_api_client.TFCloudHttpRequest(
            googleapiclient_http.HttpMockSequence([({"status": "200"}, "{}")]),
            object(),
            "fake_uri",
        )
        self.assertIsInstance(http_request, googleapiclient_http.HttpRequest)
        self.assertEqual(
            {"user-agent": "tf-cloud/" + version.__version__}, http_request.headers
        )


if __name__ == "__main__":
    tf.test.main()
