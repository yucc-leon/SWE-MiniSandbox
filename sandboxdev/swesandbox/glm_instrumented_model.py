"""GLM call-level instrumentation by wrapping the litellm call site directly.

litellm's callback hooks did NOT fire for sync completion in this version, so instead of
relying on them we subclass LitellmTextbasedModel and time/measure each `_query` call
ourselves. Set MSWEA_MODEL_CLASS=swesandbox.glm_instrumented_model.GLMInstrumentedModel
and GLM_CALLS_LOG=<path> to record per-call latency / tokens / in-flight concurrency / 429.
"""
import os, json, time, threading
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

_LK = threading.Lock()
_S = {"inflight": 0, "peak": 0, "ok": 0, "fail": 0, "n429": 0, "lat": 0.0, "ptok": 0, "ctok": 0, "t0": time.time()}
_PATH = os.environ.get("GLM_CALLS_LOG", "/tmp/glm_calls.jsonl")


def glm_summary():
    with _LK:
        el = max(time.time() - _S["t0"], 1e-9); n = max(_S["ok"], 1)
        return (f"GLM ok={_S['ok']} fail={_S['fail']} 429={_S['n429']} peak_conc={_S['peak']} "
                f"{_S['ok']/el*60:.1f}calls/min avg_lat={_S['lat']/n:.1f}s out_tok/s={_S['ctok']/el:.0f} "
                f"avg_out_tok={_S['ctok']//n}")


class GLMInstrumentedModel(LitellmTextbasedModel):
    def _query(self, messages, **kwargs):
        with _LK:
            _S["inflight"] += 1
            if _S["inflight"] > _S["peak"]:
                _S["peak"] = _S["inflight"]
        t = time.time(); err = None; resp = None
        try:
            resp = super()._query(messages, **kwargs)
            return resp
        except Exception as e:
            err = e; raise
        finally:
            lat = time.time() - t
            u = getattr(resp, "usage", None) if resp is not None else None
            pt = (getattr(u, "prompt_tokens", 0) or 0) if u is not None else 0
            ct = (getattr(u, "completion_tokens", 0) or 0) if u is not None else 0
            is429 = err is not None and ("429" in str(err) or "RateLimit" in type(err).__name__)
            with _LK:
                _S["inflight"] -= 1
                if err is None:
                    _S["ok"] += 1; _S["lat"] += lat; _S["ptok"] += pt; _S["ctok"] += ct
                else:
                    _S["fail"] += 1; _S["n429"] += int(is429)
                try:
                    with open(_PATH, "a") as f:
                        f.write(json.dumps({"t": round(time.time(), 1), "ok": int(err is None),
                                            "lat": round(lat, 2), "ptok": pt, "ctok": ct,
                                            "inflight": _S["inflight"], "is429": int(is429)}) + "\n")
                except Exception:
                    pass
