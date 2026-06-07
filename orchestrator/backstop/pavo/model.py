"""MetaController architecture (identical to experiments/exp3_train_ppo.py).

Isolated here so users can `from pavo_bench import MetaController` and load
the released checkpoint without importing the training script.
"""
from __future__ import annotations

import torch
import torch.nn as nn


class MetaController(nn.Module):
    """85,041-parameter MLP meta-controller.

    Layout:
        policy net:  Linear(state_dim -> hidden) -> ReLU ->
                     Linear(hidden    -> hidden) -> ReLU ->
                     Linear(hidden    -> n_profiles)

        value head:  Linear(state_dim -> hidden) -> ReLU ->
                     Linear(hidden    -> 1)
    """

    def __init__(self, state_dim: int = 12, hidden: int = 256, n_profiles: int = 48) -> None:
        super().__init__()
        self.state_dim = state_dim
        self.hidden = hidden
        self.n_profiles = n_profiles

        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, n_profiles),
        )
        self.value_head = nn.Sequential(
            nn.Linear(state_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def forward(self, state: torch.Tensor):
        logits = self.net(state)
        value = self.value_head(state)
        return logits, value

    def count_params(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)
