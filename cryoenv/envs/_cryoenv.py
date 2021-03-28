import numpy as np
import gym
from gym import error, spaces, utils
from gym.utils import seeding
import numpy as np
import collections


class CryoEnv(gym.Env):
    metadata = {'render.modes': ['human']}

    def __init__(self,
                 action_low=np.array([[0., 1., 0.]]),
                 # first action is V_set decrease, second is waiting time, third is reset prob
                 action_high=np.array([[100., 100., 1.]]),
                 oberservation_low=np.array([[0., 0.]]),  # first observation is V_set, second is PH
                 oberservation_high=np.array([[100., 1.]]),
                 heater_resistance=np.array([100.]),
                 thermal_link_channels=np.array([[1.]]),
                 thermal_link_heatbath=np.array([1.]),
                 temperature_heatbath=0.,
                 alpha=1.,
                 beta=1.,
                 gamma=1.,
                 s=3.,
                 v=60.,
                 g=0.001,
                 r=1.,
                 n=15,
                 control_pulse_amplitude=10,
                 env_fluctuations=1,
                 ):

        # input handling
        self.max_vset = action_high[0, 1]
        self.nmbr_channels = len(action_low)
        self.nmbr_actions = 3  # first action is V_set decrease, second is waiting time, third is reset prob
        self.nmbr_observations = 2  # first observation is V_set, second is PH
        assert action_high.shape == (
            self.nmbr_channels, self.nmbr_actions), "action_high must have same shape as action_low!"
        assert oberservation_low.shape == (
            self.nmbr_channels, self.nmbr_observations), "oberservation_low must have same length as action_low!"
        assert oberservation_high.shape == (
            self.nmbr_channels, self.nmbr_observations), "oberservation_high must have same shape as oberservation_low!"
        assert len(heater_resistance) == self.nmbr_channels, "heater_resistance must have same length as action_low!"
        assert thermal_link_channels.shape == (
            self.nmbr_channels,
            self.nmbr_channels), "thermal_link_channels must have shape (nmbr_channels, nmbr_channels)!"
        assert len(
            thermal_link_heatbath) == self.nmbr_channels, "thermal_link_heatbath must have same length as action_low!"

        # create action and observation spaces
        self.action_space = spaces.Box(low=action_low.reshape(-1),
                                       high=action_high.reshape(-1),
                                       dtype=np.float32)
        self.observation_space = spaces.Box(low=oberservation_low.reshape(-1),
                                            high=oberservation_high.reshape(-1),
                                            dtype=np.float32)

        # environment parameters
        self.heater_resistance = np.array(heater_resistance)
        self.thermal_link_channels = np.array(thermal_link_channels)
        self.thermal_link_heatbath = np.array(thermal_link_heatbath)
        self.temperature_heatbath = np.array(temperature_heatbath)
        self.g = g
        self.hysteresis = np.zeros(self.nmbr_channels, dtype=bool)
        self.control_pulse_amplitude = control_pulse_amplitude
        self.env_fluctuations = env_fluctuations

        # reward parameters
        self.r = r
        self.s = s
        self.v = v
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.n = n
        self.last_phs = collections.deque(maxlen=self.n)

        # sensor parameters
        def check_sensor_pars(k, T0):
            return self.sensor_model(0, k, T0) > 0.1 and \
                   self.sensor_model(0, k, T0) < 0.5 and \
                   self.sensor_model(1, k, T0) > 0.999

        k = np.empty(self.nmbr_channels)
        T0 = np.empty(self.nmbr_channels)

        for i in range(self.nmbr_channels):
            good_pars = False
            while not good_pars:
                k[i], T0[i] = np.random.uniform(low=5, high=50), np.random.uniform(low=0, high=1)
                good_pars = check_sensor_pars(k[i], T0[i])

        self.k = k
        self.t0 = T0

        # initial state
        self.state = self.reset()

    def sensor_model(self, T, k, T0):
        return 1 / (1 + np.exp(-k * (T - T0)))

    def temperature_model(self, P_R, P_E):
        T = (self.thermal_link_channels * self.temperature_heatbath + P_R + P_E)
        T = np.linalg.inv(np.diag(self.thermal_link_heatbath) + self.thermal_link_channels - np.diag(
            self.thermal_link_channels @ np.ones(self.nmbr_channels))) @ T
        return T.flatten()

    def environment_model(self, state):
        return np.random.normal(mean=0, scale=self.env_fluctuations, size=1)

    def reward(self, new_state, action):

        reward = 0

        for (s, a) in zip(new_state.reshape(-1, self.nmbr_observations),
                          action.reshape(-1, self.nmbr_actions)):

            # unpack action
            dV = a[0]
            w = a[1]
            z = a[2]

            # unpack new state
            V_set_new = s[0]
            ph_new = s[1]

            # check stability
            if np.abs(ph_new - np.mean(self.last_phs)) < s * np.std(self.last_phs):
                stable = True
                self.last_phs.append(ph_new)
            else:
                stable = False

            # reset case
            if z > 0.5:
                reward -= self.gamma * self.v  # detector needs to go back to normal conducting phase

            # normal case
            else:
                reward -= self.alpha / w / ph_new  # detector range maximization
                reward -= self.beta / w * np.std(self.last_phs)  # detector sigma minimization
                reward -= self.gamma * (1 / w + self.r * dV)  # dead time due to sending of pulse and ramping
                if not stable:
                    reward -= self.gamma  # penalty for instability

        return reward

    def step(self, action):

        # get the next state
        new_state = np.empty(self.state.shape, dtype=self.state.dtype)

        for c, (s, a) in enumerate(
                zip(self.state.reshape(-1, self.nmbr_observations), action.reshape(-1, self.nmbr_actions))):

            # unpack action
            dV = a[0]
            w = a[1]
            z = a[2]

            # unpack state
            V_set = s[0]
            ph = s[1]

            # reset case
            if z > 0.5:
                new_state[c, :] = np.array([self.max_vset, self.g])
                self.hysteresis[c] = False
            else:

                # new Vset
                new_state[c, 0] = V_set - dV

                # new ph
                if self.hysteresis[c]:
                    new_state[c, 1] = self.g
                else:

                    # get long scale environment fluctuations
                    P_E_long = self.environment_model(self.state)

                    # height without signal
                    P_R = new_state[c, 0] / self.heater_resistance[c]  # voltage goes through square rooter
                    T = self.temperature_model(P_R=P_R,
                                               P_E=self.environment_model(self.state) + P_E_long)
                    height_baseline = self.sensor_model(T, self.k[c], self.T0[c])

                    # height with signal
                    P_R_inj = np.sqrt(new_state[c, 0] ** 2 + self.control_pulse_amplitude ** 2) / \
                              self.heater_resistance[c]  # voltage goes through square rooter
                    T_inj = self.temperature_model(P_R=P_R_inj,
                                                   P_E=self.environment_model(self.state) + P_E_long)
                    height_signal = self.sensor_model(T_inj, self.k[c], self.T0[c])

                    # difference is pulse height
                    new_state[c, 1] = height_signal - height_baseline

        # get the reward
        reward = self.reward(new_state, action)

        # update state
        self.state = new_state

        # the task is continuing
        done = False

        info = {}

        return new_state, reward, done, info

    def reset(self):
        self.state = np.array([[self.max_vset, self.g] * self.nmbr_channels]).reshape(-1)
        self.hysteresis[:] = False
        return self.state

    def render(self, mode='human'):
        pass

    def close(self):
        pass
