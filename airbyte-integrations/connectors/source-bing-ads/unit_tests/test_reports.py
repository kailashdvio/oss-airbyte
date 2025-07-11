#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#

import _csv
import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import urlparse

import pendulum
import pytest
import source_bing_ads
from bingads.service_info import SERVICE_INFO_DICT_V13
from bingads.v13.internal.reporting.row_report import _RowReport
from bingads.v13.internal.reporting.row_report_iterator import _RowReportRecord, _RowValues
from conftest import find_stream
from helpers import source
from source_bing_ads.base_streams import Accounts
from source_bing_ads.report_streams import (
    AccountPerformanceReportDaily,
    AccountPerformanceReportHourly,
    AccountPerformanceReportMonthly,
    SearchQueryPerformanceReportDaily,
    SearchQueryPerformanceReportHourly,
)
from source_bing_ads.reports import BingAdsReportingServicePerformanceStream, BingAdsReportingServiceStream
from source_bing_ads.reports.ad_performance_report import (
    AdPerformanceReportDaily,
    AdPerformanceReportHourly,
)
from source_bing_ads.source import SourceBingAds
from suds import WebFault

from airbyte_cdk.models import SyncMode


TEST_CONFIG = {
    "developer_token": "developer_token",
    "client_id": "client_id",
    "refresh_token": "refresh_token",
    "reports_start_date": "2020-01-01T00:00:00Z",
}


class TestClient:
    pass


class TestReport(BingAdsReportingServiceStream, SourceBingAds):
    date_format, report_columns, report_name, cursor_field = "YYYY-MM-DD", None, None, "Time"
    report_aggregation = "Monthly"
    report_schema_name = "campaign_performance_report"

    def __init__(self) -> None:
        self.client = TestClient()


class TestPerformanceReport(BingAdsReportingServicePerformanceStream, SourceBingAds):
    date_format, report_columns, report_name, cursor_field = "YYYY-MM-DD", None, None, "Time"
    report_aggregation = "Monthly"
    report_schema_name = "campaign_performance_report"

    def __init__(self) -> None:
        self.client = TestClient()


@patch.object(source_bing_ads.source, "Client")
def test_AccountPerformanceReportMonthly_request_params(mocked_client, config):
    accountperformancereportmonthly = AccountPerformanceReportMonthly(mocked_client, config)
    request_params = accountperformancereportmonthly.request_params(account_id=180278106, stream_slice={"time_period": "ThisYear"})
    del request_params["report_request"]
    assert request_params == {
        "overwrite_result_file": True,
        "result_file_directory": "/tmp",
        "result_file_name": "AccountPerformanceReport",
        "timeout_in_milliseconds": 300000,
    }


def test_get_updated_state_init_state():
    test_report = TestReport()
    stream_state = {}
    latest_record = {"AccountId": 123, "Time": "2020-01-02"}
    new_state = test_report.get_updated_state(stream_state, latest_record)
    assert new_state["123"]["Time"] == "2020-01-02"


def test_get_updated_state_new_state():
    test_report = TestReport()
    stream_state = {"123": {"Time": "2020-01-01"}}
    latest_record = {"AccountId": 123, "Time": "2020-01-02"}
    new_state = test_report.get_updated_state(stream_state, latest_record)
    assert new_state["123"]["Time"] == "2020-01-02"


def test_get_updated_state_state_unchanged():
    test_report = TestReport()
    stream_state = {"123": {"Time": "2020-01-03"}}
    latest_record = {"AccountId": 123, "Time": "2020-01-02"}
    new_state = test_report.get_updated_state(copy.deepcopy(stream_state), latest_record)
    assert stream_state == new_state


def test_get_updated_state_state_new_account():
    test_report = TestReport()
    stream_state = {"123": {"Time": "2020-01-03"}}
    latest_record = {"AccountId": 234, "Time": "2020-01-02"}
    new_state = test_report.get_updated_state(stream_state, latest_record)
    assert "234" in new_state and "123" in new_state
    assert new_state["234"]["Time"] == "2020-01-02"


@pytest.mark.parametrize(
    "stream_report_daily_cls",
    (
        AccountPerformanceReportDaily,
        AdPerformanceReportDaily,
        SearchQueryPerformanceReportDaily,
    ),
)
def test_get_report_record_timestamp_daily(stream_report_daily_cls):
    stream_report = stream_report_daily_cls(client=Mock(), config=TEST_CONFIG)
    assert "2020-01-01" == stream_report.get_report_record_timestamp("2020-01-01")


def test_get_report_record_timestamp_without_aggregation(config, mock_user_query, mock_auth_token):
    stream_report = find_stream("budget_summary_report", config)
    record = {"Date": "08/13/2024"}
    expected_record = {"Date": "2024-08-13"}
    transformed_record = list(
        stream_report.retriever.record_selector.filter_and_transform(all_data=[record], stream_state={}, stream_slice={}, records_schema={})
    )[0]
    assert transformed_record["Date"] == expected_record["Date"]


@pytest.mark.parametrize(
    "stream_report_hourly_cls",
    (
        AccountPerformanceReportHourly,
        AdPerformanceReportHourly,
        SearchQueryPerformanceReportHourly,
    ),
)
def test_get_report_record_timestamp_hourly(stream_report_hourly_cls):
    stream_report = stream_report_hourly_cls(client=Mock(), config=TEST_CONFIG)
    assert "2020-01-01T15:00:00+00:00" == stream_report.get_report_record_timestamp("2020-01-01|15")


def test_report_parse_response_csv_error(caplog):
    stream_report = AccountPerformanceReportHourly(client=Mock(), config=TEST_CONFIG)
    fake_response = MagicMock()
    fake_response.report_records.__iter__ = MagicMock(side_effect=_csv.Error)
    list(stream_report.parse_response(fake_response))
    assert (
        "CSV report file for stream `account_performance_report_hourly` is broken or cannot be read correctly: , skipping ..."
        in caplog.messages
    )


@patch.object(source_bing_ads.source, "Client")
def test_custom_report_clear_namespace(mocked_client, config_with_custom_reports, logger_mock):
    custom_report = source(config_with_custom_reports).get_custom_reports(config_with_custom_reports, mocked_client)[0]
    assert custom_report._clear_namespace("tns:ReportAggregation") == "ReportAggregation"


@patch.object(source_bing_ads.source, "Client")
def test_custom_report_get_object_columns(mocked_client, config_with_custom_reports, logger_mock):
    reporting_service_mock = MagicMock()
    reporting_service_mock._get_service_info_dict.return_value = SERVICE_INFO_DICT_V13
    mocked_client.get_service.return_value = reporting_service_mock
    mocked_client.environment = "production"

    custom_report = source(config=config_with_custom_reports).get_custom_reports(config_with_custom_reports, mocked_client)[0]

    tree = ET.parse(urlparse(SERVICE_INFO_DICT_V13[("reporting", mocked_client.environment)]).path)
    request_object = tree.find(f".//{{*}}complexType[@name='{custom_report.report_name}Request']")

    assert custom_report._get_object_columns(request_object, tree) == [
        "TimePeriod",
        "AccountId",
        "AccountName",
        "AccountNumber",
        "AccountStatus",
        "CampaignId",
        "CampaignName",
        "CampaignStatus",
        "AdGroupId",
        "AdGroupName",
        "AdGroupStatus",
        "AdDistribution",
        "Language",
        "Network",
        "TopVsOther",
        "DeviceType",
        "DeviceOS",
        "BidStrategyType",
        "TrackingTemplate",
        "CustomParameters",
        "DynamicAdTargetId",
        "DynamicAdTarget",
        "DynamicAdTargetStatus",
        "WebsiteCoverage",
        "Impressions",
        "Clicks",
        "Ctr",
        "AverageCpc",
        "Spend",
        "AveragePosition",
        "Conversions",
        "ConversionRate",
        "CostPerConversion",
        "Assists",
        "Revenue",
        "ReturnOnAdSpend",
        "CostPerAssist",
        "RevenuePerConversion",
        "RevenuePerAssist",
        "AllConversions",
        "AllRevenue",
        "AllConversionRate",
        "AllCostPerConversion",
        "AllReturnOnAdSpend",
        "AllRevenuePerConversion",
        "ViewThroughConversions",
        "Goal",
        "GoalType",
        "AbsoluteTopImpressionRatePercent",
        "TopImpressionRatePercent",
        "AverageCpm",
        "ConversionsQualified",
        "AllConversionsQualified",
        "ViewThroughConversionsQualified",
        "AdId",
        "ViewThroughRevenue",
    ]


@patch.object(source_bing_ads.source, "Client")
def test_custom_report_send_request(mocked_client, config_with_custom_reports, logger_mock, caplog):
    class Fault:
        faultstring = "Invalid Client Data"

    custom_report = source(config=config_with_custom_reports).get_custom_reports(config_with_custom_reports, mocked_client)[0]
    with patch.object(BingAdsReportingServiceStream, "send_request", side_effect=WebFault(fault=Fault(), document=None)):
        custom_report.send_request(params={}, customer_id="13131313", account_id="800800808")
        assert (
            "Could not sync custom report my test custom report: Please validate your column and aggregation configuration. "
            "Error form server: [Invalid Client Data]"
        ) in caplog.text


@pytest.mark.parametrize(
    "aggregation, datastring, expected",
    [
        (
            "Summary",
            "11/13/2023",
            "2023-11-13",
        ),
        (
            "Hourly",
            "2022-11-13|10",
            "2022-11-13T10:00:00+00:00",
        ),
        (
            "Daily",
            "2022-11-13",
            "2022-11-13",
        ),
        (
            "Weekly",
            "2022-11-13",
            "2022-11-13",
        ),
        (
            "Monthly",
            "2022-11-13",
            "2022-11-13",
        ),
        (
            "WeeklyStartingMonday",
            "2022-11-13",
            "2022-11-13",
        ),
    ],
)
@patch.object(source_bing_ads.source, "Client")
def test_custom_report_get_report_record_timestamp(mocked_client, config_with_custom_reports, aggregation, datastring, expected):
    custom_report = source(config=config_with_custom_reports).get_custom_reports(config_with_custom_reports, mocked_client)[0]
    custom_report.report_aggregation = aggregation
    assert custom_report.get_report_record_timestamp(datastring) == expected


@patch.object(source_bing_ads.source, "Client")
def test_account_performance_report_monthly_stream_slices(mocked_client, config_without_start_date):
    mocked_client.reports_start_date = None
    account_performance_report_monthly = AccountPerformanceReportMonthly(mocked_client, config_without_start_date)
    accounts_read_records = iter([{"Id": 180519267, "ParentCustomerId": 100}, {"Id": 180278106, "ParentCustomerId": 200}])
    with patch.object(Accounts, "read_records", return_value=accounts_read_records):
        stream_slice = list(account_performance_report_monthly.stream_slices(sync_mode=SyncMode.full_refresh))
        assert stream_slice == [
            {"account_id": 180519267, "customer_id": 100, "time_period": "LastYear"},
            {"account_id": 180519267, "customer_id": 100, "time_period": "ThisYear"},
            {"account_id": 180278106, "customer_id": 200, "time_period": "LastYear"},
            {"account_id": 180278106, "customer_id": 200, "time_period": "ThisYear"},
        ]


@patch.object(source_bing_ads.source, "Client")
def test_account_performance_report_monthly_stream_slices_no_time_period(mocked_client, config):
    account_performance_report_monthly = AccountPerformanceReportMonthly(mocked_client, config)
    accounts_read_records = iter([{"Id": 180519267, "ParentCustomerId": 100}, {"Id": 180278106, "ParentCustomerId": 200}])
    with patch.object(Accounts, "read_records", return_value=accounts_read_records):
        stream_slice = list(account_performance_report_monthly.stream_slices(sync_mode=SyncMode.full_refresh))
        assert stream_slice == [{"account_id": 180519267, "customer_id": 100}, {"account_id": 180278106, "customer_id": 200}]


@pytest.mark.parametrize(
    "aggregation",
    [
        "DayOfWeek",
        "HourOfDay",
    ],
)
@patch.object(source_bing_ads.source, "Client")
def test_custom_performance_report_no_last_year_stream_slices(mocked_client, config_with_custom_reports, aggregation):
    mocked_client.reports_start_date = None  # in case of start date time period won't be used in request params
    custom_report = source(config=config_with_custom_reports).get_custom_reports(config_with_custom_reports, mocked_client)[0]
    custom_report.report_aggregation = aggregation
    accounts_read_records = iter([{"Id": 180519267, "ParentCustomerId": 100}, {"Id": 180278106, "ParentCustomerId": 200}])
    with patch.object(Accounts, "read_records", return_value=accounts_read_records):
        stream_slice = list(custom_report.stream_slices(sync_mode=SyncMode.full_refresh))
        assert stream_slice == [
            {"account_id": 180519267, "customer_id": 100, "time_period": "ThisYear"},
            {"account_id": 180278106, "customer_id": 200, "time_period": "ThisYear"},
        ]


@pytest.mark.parametrize(
    "stream, response, records",
    [
        (AccountPerformanceReportHourly, "hourly_reports/account_performance.csv", "hourly_reports/account_performance_records.json"),
        (AdPerformanceReportHourly, "hourly_reports/ad_performance.csv", "hourly_reports/ad_performance_records.json"),
        (
            SearchQueryPerformanceReportHourly,
            "hourly_reports/search_query_performance.csv",
            "hourly_reports/search_query_performance_records.json",
        ),
    ],
)
@patch.object(source_bing_ads.source, "Client")
def test_hourly_reports(mocked_client, config, stream, response, records):
    stream_object = stream(mocked_client, config)
    with patch.object(stream, "send_request", return_value=_RowReport(file=Path(__file__).parent / response)):
        with open(Path(__file__).parent / records, "r") as file:
            assert list(stream_object.read_records(sync_mode=SyncMode.full_refresh, stream_slice={}, stream_state={})) == json.load(file)
