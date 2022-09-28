# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.



from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Optional, Tuple, cast

import gym
import numpy as np
import torch
import torch.nn as nn
from gym.spaces import Discrete
from tianshou.data import Batch, ReplayBuffer, to_torch
from tianshou.policy import BasePolicy, PPOPolicy

__all__ = ["AllOne", "PPO"]


# baselines #


class NonLearnablePolicy(BasePolicy):
    """Tianshou's BasePolicy with empty ``learn`` and ``process_fn``.

    This could be moved outside in future.
    """

    def __init__(self, obs_space , action_space )  :
        super().__init__()

    def learn(self, batch , **kwargs )   :
        pass

    def process_fn(
        self,
        batch ,
        buffer ,
        indices ,
    )  :
        pass


class AllOne(NonLearnablePolicy):
    """Forward returns a batch full of 1.

    Useful when implementing some baselines (e.g., TWAP).
    """

    def forward(
        self,
        batch ,
        state      = None,
        **kwargs ,
    )  :
        return Batch(act=np.full(len(batch), 1.0), state=state)


# ppo #


class PPOActor(nn.Module):
    def __init__(self, extractor , action_dim )  :
        super().__init__()
        self.extractor = extractor
        self.layer_out = nn.Sequential(nn.Linear(cast(int, extractor.output_dim), action_dim), nn.Softmax(dim=-1))

    def forward(
        self,
        obs ,
        state  = None,
        info  = {},
    )   :
        feature = self.extractor(to_torch(obs, device=auto_device(self)))
        out = self.layer_out(feature)
        return out, state


class PPOCritic(nn.Module):
    def __init__(self, extractor )  :
        super().__init__()
        self.extractor = extractor
        self.value_out = nn.Linear(cast(int, extractor.output_dim), 1)

    def forward(
        self,
        obs ,
        state  = None,
        info  = {},
    )  :
        feature = self.extractor(to_torch(obs, device=auto_device(self)))
        return self.value_out(feature).squeeze(dim=-1)


class PPO(PPOPolicy):
    """A wrapper of tianshou PPOPolicy.

    Differences:

    - Auto-create actor and critic network. Supports discrete action space only.
    - Dedup common parameters between actor network and critic network
      (not sure whether this is included in latest tianshou or not).
    - Support a ``weight_file`` that supports loading checkpoint.
    - Some parameters' default values are different from original.
    """

    def __init__(
        self,
        network ,
        obs_space ,
        action_space ,
        lr ,
        weight_decay  = 0.0,
        discount_factor  = 1.0,
        max_grad_norm  = 100.0,
        reward_normalization  = True,
        eps_clip  = 0.3,
        value_clip  = True,
        vf_coef  = 1.0,
        gae_lambda  = 1.0,
        max_batch_size  = 256,
        deterministic_eval  = True,
        weight_file  = None,
    )  :
        assert isinstance(action_space, Discrete)
        actor = PPOActor(network, action_space.n)
        critic = PPOCritic(network)
        optimizer = torch.optim.Adam(
            chain_dedup(actor.parameters(), critic.parameters()),
            lr=lr,
            weight_decay=weight_decay,
        )
        super().__init__(
            actor,
            critic,
            optimizer,
            torch.distributions.Categorical,
            discount_factor=discount_factor,
            max_grad_norm=max_grad_norm,
            reward_normalization=reward_normalization,
            eps_clip=eps_clip,
            value_clip=value_clip,
            vf_coef=vf_coef,
            gae_lambda=gae_lambda,
            max_batchsize=max_batch_size,
            deterministic_eval=deterministic_eval,
            observation_space=obs_space,
            action_space=action_space,
        )
        if weight_file is not None:
            load_weight(self, weight_file)


# utilities: these should be put in a separate (common) file. #


def auto_device(module )  :
    for param in module.parameters():
        return param.device
    return torch.device("cpu")  # fallback to cpu


def load_weight(policy , path )  :
    assert isinstance(policy, nn.Module), "Policy has to be an nn.Module to load weight."
    loaded_weight = torch.load(path, map_location="cpu")
    try:
        policy.load_state_dict(loaded_weight)
    except RuntimeError:
        # try again by loading the converted weight
        # https://github.com/thu-ml/tianshou/issues/468
        for k in list(loaded_weight):
            loaded_weight["_actor_critic." + k] = loaded_weight[k]
        policy.load_state_dict(loaded_weight)


def chain_dedup(*iterables )    :
    seen = set()
    for iterable in iterables:
        for i in iterable:
            if i not in seen:
                seen.add(i)
                yield i
