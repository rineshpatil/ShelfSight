import httpx
import pytest
import respx
from tenacity import wait_none

from shelfsight.engines.base import Pacer, RetryableError, post_json, retrying


def test_pacer_sleeps_only_the_remaining_gap():
    t, slept = [100.0], []
    p = Pacer(5, clock=lambda: t[0], sleep=slept.append)
    p.wait()
    t[0] = 102.0
    p.wait()
    t[0] = 111.0
    p.wait()
    assert slept == [3.0]


def test_retryable_error_keeps_enough_body_to_name_the_quota():
    body = "x" * 500 + "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    assert "PerDayPerProjectPerModel-FreeTier" in str(RetryableError(429, body))


@respx.mock
def test_4xx_other_than_429_is_not_retried():
    route = respx.post("https://x.test/").mock(return_value=httpx.Response(400, text="bad"))
    with httpx.Client() as c, pytest.raises(httpx.HTTPStatusError):
        retrying(wait_none())(post_json, c, "https://x.test/", {}, {}, Pacer(0))
    assert route.call_count == 1


@respx.mock
def test_5xx_is_retried_four_times_then_raised():
    route = respx.post("https://x.test/").mock(return_value=httpx.Response(503, text="down"))
    with httpx.Client() as c, pytest.raises(RetryableError) as e:
        retrying(wait_none())(post_json, c, "https://x.test/", {}, {}, Pacer(0))
    assert e.value.status == 503
    assert route.call_count == 4
