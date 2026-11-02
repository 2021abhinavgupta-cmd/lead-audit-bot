"""History summary: SES delivery stats are summed, cached, and unknown (None) when AWS cannot answer."""
import app as app_module


class _Client:
    def __init__(self, points=None, boom=False):
        self.points, self.boom, self.calls = points or [], boom, 0

    def get_send_statistics(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("no aws")
        return {"SendDataPoints": self.points}


def _use(monkeypatch, client):
    monkeypatch.setattr(app_module.ses, "client", client, raising=False)
    app_module._ses_stats_cache.update(at=0.0, value=None)


def test_ses_stats_are_summed_across_datapoints(monkeypatch):
    _use(monkeypatch, _Client([{"DeliveryAttempts": 10, "Bounces": 1, "Complaints": 0, "Rejects": 0},
                               {"DeliveryAttempts": 5, "Bounces": 2, "Complaints": 1}]))
    assert app_module._ses_delivery_stats() == {"deliveryattempts": 15, "bounces": 3, "complaints": 1, "rejects": 0}


def test_an_aws_failure_is_unknown_not_zero(monkeypatch):
    _use(monkeypatch, _Client(boom=True))
    assert app_module._ses_delivery_stats() is None


def test_the_result_is_cached_for_five_minutes(monkeypatch):
    c = _Client([{"Bounces": 1}])
    _use(monkeypatch, c)
    app_module._ses_delivery_stats(); app_module._ses_delivery_stats()
    assert c.calls == 1
