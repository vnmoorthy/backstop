"""SignalExtractor: a CallTurn -> the 12-dim state vector the PAVO router expects.

Layout matches the released training env (exp3_train_ppo.py::RoutingEnvironment):
    [ SNR/50, speaking_rate, pitch_var, WADA_SNR_proxy,
      cpu_util, ram_fraction, battery, gpu_util,
      RTT/200, bandwidth_proxy,
      complexity/5, ctx_tokens/2000 ]

Only the acoustic/complexity/context dims vary per call turn; the hardware/network
dims are held at the training-time defaults (the released policy was trained that
way). The two dims that move the routing decision under the coupling mask are
complexity (dim 10) and SNR (dim 0).
"""
from __future__ import annotations

import numpy as np

# training-time defaults for the dims that don't vary per turn
_SPEAKING_RATE = 4.0 / 10.0
_PITCH_VAR = 0.5
_CPU = 0.3
_RAM = 0.8
_BATTERY = 0.9
_GPU = 0.3
_RTT = 0.1
_BW = 0.5


def turn_to_state(turn) -> np.ndarray:
    """turn: any object exposing .snr_db, .complexity, .ctx_tokens."""
    snr = max(0.0, min(float(turn.snr_db), 50.0)) / 50.0
    return np.asarray(
        [
            snr,  # 0 SNR
            _SPEAKING_RATE,  # 1
            _PITCH_VAR,  # 2
            snr,  # 3 WADA proxy
            _CPU,  # 4
            _RAM,  # 5
            _BATTERY,  # 6
            _GPU,  # 7
            _RTT,  # 8
            _BW,  # 9
            float(turn.complexity) / 5.0,  # 10 complexity
            float(turn.ctx_tokens) / 2000.0,  # 11 context depth
        ],
        dtype=np.float32,
    )
