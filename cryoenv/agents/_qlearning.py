from ..base import Agent
import numpy as np


class QLearning(Agent):

    def __init__(self, env, policy, value_function):
        super(QLearning, self).__init__(env, policy, value_function)

    def learn(self, nmbr_steps, learning_rate, discount_factor, **kwargs):
        """
        We perform nmbr_steps with the agent on the environment and update the value function and policy.
        """

        assert 'max_epsilon' in kwargs and 'min_epsilon' in kwargs, 'You need to put max_epsilon and min_epslon as arguments!'

        obs = self.env.reset()
        total_training_rewards = 0

        for step in range(nmbr_steps):

            action = self.policy.predict(obs)
            new_obs, reward, done, info = self.env.step(action)

            self.value_function.update(action=action,
                                       observation=obs,
                                       new_value=(1 - learning_rate) * self.value_function.predict(obs, action) +
                                                 learning_rate * (reward +
                                                                  discount_factor * self.value_function.greedy(
                                                   new_obs)))

            total_training_rewards += reward
            obs = new_obs

            if done == True:
                break

            # Cutting down on exploration by reducing the epsilon
            self.policy.update(
                epsilon=(1 - step / nmbr_steps) * kwargs['max_epsilon'] + step / nmbr_steps * kwargs['min_epsilon'])