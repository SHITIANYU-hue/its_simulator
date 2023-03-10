import argparse
import logging
import os
from wolf.world.environments.wolfenv.agents.connectors.action.exchange_change_phase import EXTEND
from wolf.world.environments.wolfenv.car_following_env import CarFollowingEnv, CarFollowingNetwork, CarFollowingStraightNetwork, CustomizedGippsController, CustomizedIDMController, CustomizedDummyController
from wolf.world.environments.wolfenv.wolf_env import WolfEnv
from wolf.world.environments.wolfenv.agents.connectors.action.exchange_change_phase import EXTEND
from flow.core.params import SumoParams, EnvParams, InitialConfig, NetParams, \
    InFlows, SumoCarFollowingParams, SumoLaneChangeParams, TrafficLightParams, VehicleParams

import random

from flow.controllers import RLController, ContinuousRouter, \
    SimLaneChangeController, IDMController
import gym
import numpy as np
import torch
import pandas as pd
import pickle as pkl
import re

from ddpg import DDPG

# Create logger
logger = logging.getLogger('test')
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

# Libdom raises an error if this is not set to true on Mac OSX
# see https://github.com/openai/spinningup/issues/16 for more information
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

def constrain_action(state, accel, epsilon=0.5):
    veh_speed, rel_speed, distance_headway, speed_limit, prev_accel = state.cpu().numpy()[0]
    if distance_headway < 20 and accel > 0:
        accel=np.clip(-2*accel, -3, 3)
    elif veh_speed >= speed_limit:
        if veh_speed > speed_limit + epsilon:
            accel = np.clip([speed_limit - veh_speed], -3, 3)
        else:
            accel = np.clip(accel, -3, 0)
    elif distance_headway > 200:
        if veh_speed <= speed_limit - epsilon:
            accel = np.clip([speed_limit - veh_speed], -3, 3)
    return accel

def batch_constrain_action(state, accels, epsilon=0.5):
    veh_speed, rel_speed, distance_headway, speed_limit, prev_accel = np.split(state.cpu().numpy(), 5, axis=1)

    condition_1 = (distance_headway < 20) & (accels > 0)
    condition_2 = (veh_speed > speed_limit + epsilon)
    condition_3 = (veh_speed >= speed_limit) & (veh_speed <= speed_limit + epsilon)
    condition_4 = (distance_headway > 200) & (veh_speed <= speed_limit - epsilon)

    accels = np.clip(np.where(condition_1, -2*accels, accels), -3, 3)
    accels = np.where(~condition_1 & condition_2, np.clip(speed_limit-veh_speed, -3, 0), accels)
    accels = np.where(~condition_1 & ~condition_2 & condition_3, np.clip(accels, -3, 0), accels)
    accels = np.where(~condition_1 & ~condition_2 & ~condition_3 & condition_4, np.clip(speed_limit - veh_speed, 0, 3), accels)

    return accels

# Parse given arguments
parser = argparse.ArgumentParser()

parser.add_argument("--seed", default=0, help="Random seed")
parser.add_argument("--save_dir", default="./saved_modelsnew/", help="Dir. path to load a model")
parser.add_argument("--load_model", help="Path to load a model")
parser.add_argument("--episodes", default=1, help="Num. of test episodes")
parser.add_argument("--network", default="loop", choices=['loop', 'straight'])
parser.add_argument("--speed-limit", default=None, type=int)
parser.add_argument("--speed-reward", default=True, action='store_true')
parser.add_argument("--no-speed-reward", dest="speed_reward", action='store_false')
parser.add_argument("--controller", default="rl", choices=["rl", "idm", "gipps"])
parser.add_argument("--lv", default=True, action="store_true", help="Enable leading vehicle [Enabled by default]")
parser.add_argument("--no-lv", dest="lv", action="store_false", help="Disable leading vehicle")
parser.add_argument("--constrain", default=True, action="store_true", help="Enable constrain [Enabled by default]")
parser.add_argument("--no-constrain", dest="constrain", action="store_false", help="Disable constrain")
parser.add_argument("--perturbation-test", default=False, action="store_true", help="Test perturbation, this will change all initial speed to 25m/s")
parser.add_argument("--perturbation-rl-num", default=8, type=int, help="Set the number of rl vehicles in the perturbation test")
parser.add_argument("--initial-speed", default=15, type=float, help="Set the initial speed of vehicles in the perturbation test")
parser.add_argument("--render", default=False, action="store_true", help="Enable the render of the simulator")
args = parser.parse_args()

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

# Some parameters, which are not saved in the network files
gamma = 0.99  # discount factor for reward (default: 0.99)
tau = 0.001  # discount factor for model (default: 0.001)
hidden_size = (400, 300)  # size of the hidden layers (Deepmind: 400 and 300; OpenAI: 64)



# time horizon of a single rollout
HORIZON = 1000
# number of parallel workers
N_CPUS = 2
# number of rollouts per training iteration
N_ROLLOUTS = N_CPUS * 4

SCALING = 1
NUM_LANES = 4 * SCALING  # number of lanes in the widest highway
DISABLE_TB = True
DISABLE_RAMP_METER = True
AV_FRAC = 0.10
reward_threshold=0


vehicles = VehicleParams()

random.seed(0)
np.random.seed(0)


if args.controller == 'rl':
    controller = (RLController, {})
elif args.controller == 'gipps':
    controller = (CustomizedGippsController, {"acc": 3})
elif args.controller == 'idm':
    controller = (CustomizedIDMController, {"a": 3, "T": 1.26})

if args.perturbation_test:
    vehicles.add(
    veh_id="human_follower",
    # acceleration_controller=(RLController, {}),
    lane_change_controller=(SimLaneChangeController, {}),
    acceleration_controller=(CustomizedIDMController, {}),
    routing_controller=(ContinuousRouter, {}),
    car_following_params=SumoCarFollowingParams(
        speed_mode="all_checks",
        accel=3,
        decel=3,
        max_speed=30
    ),
    lane_change_params=SumoLaneChangeParams(
        lane_change_mode=0,
    ),
    num_vehicles=1,
    initial_speed=0 if not args.perturbation_test else args.initial_speed)


vehicles.add(
    veh_id="followerstopper",
    acceleration_controller=controller,
    # lane_change_controller=(SimLaneChangeController, {}),
    routing_controller=(ContinuousRouter, {}),
    car_following_params=SumoCarFollowingParams(
        speed_mode="aggressive",
        accel=3,
        decel=3,
    ),
    # lane_change_params=SumoLaneChangeParams(
    #     lane_change_mode=0,
    # ),
    num_vehicles=1 if not args.perturbation_test else 10,
    initial_speed=0 if not args.perturbation_test else args.initial_speed) # set initial speed

if args.lv:
    vehicles.add(
        veh_id="human",
        # acceleration_controller=(RLController, {}),
        lane_change_controller=(SimLaneChangeController, {}),
        acceleration_controller=(CustomizedDummyController, {'v_des': args.initial_speed}) if args.perturbation_test else (CustomizedIDMController, {}),
        routing_controller=(ContinuousRouter, {}),
        car_following_params=SumoCarFollowingParams(
            speed_mode="aggressive" if args.perturbation_test else "all_checks",
            accel=3,
            decel=3,
            max_speed=30
        ),
        lane_change_params=SumoLaneChangeParams(
            lane_change_mode=0,
        ),
        num_vehicles=1,
        initial_speed=0 if not args.perturbation_test else args.initial_speed)


additional_env_params = {
    # For ADDITIONAL_ENV_PARAMS
    "max_accel": 3,
    "max_decel": 3,
    "lane_change_duration": 5,
    "disable_tb": DISABLE_TB,
    "disable_ramp_metering": DISABLE_RAMP_METER,
    # For ADDITIONAL_RL_ENV_PARAMS
    "target_velocity": 40,
    "add_rl_if_exit": False
}

# flow rate
flow_rate = 200 * SCALING

# percentage of flow coming out of each lane
# inflow = InFlows()
# inflow.add(
#     veh_type="human",
#     edge="1",
#     vehs_per_hour=flow_rate * (1 - AV_FRAC),
#     departLane="random",
#     departSpeed=0)


traffic_lights = TrafficLightParams()

additional_net_params = {"scaling": SCALING, "speed_limit": args.speed_limit, "length": 985, "width": 100, "rl_vehs": 2}
net_params = NetParams(
    # inflows=inflow,
    additional_params=additional_net_params)

if args.network == 'loop':
    network = CarFollowingNetwork(
        name='car_following',
        vehicles=vehicles,
        net_params=net_params,
        initial_config=InitialConfig(edges_distribution=['1', '2']), # We only want the vehicle be place in the first edge
        traffic_lights=traffic_lights,
    )
elif args.network == 'straight':
    network = CarFollowingStraightNetwork(
        name='car_following2',
        vehicles=vehicles,
        net_params=net_params,
        initial_config=InitialConfig(edges_distribution=["1", "2"]),
        traffic_lights=traffic_lights
    )

env_config = {
    "simulator": "traci",

    "sim_params": SumoParams(
        sim_step=0.1,
        render=args.render,
        print_warnings=False,
        restart_instance=True,
        emission_path='/tmp/yifei'
    ),

    "env_params": EnvParams(
        warmup_steps=200 if args.network == 'loop' else 50,
        sims_per_step=1,
        horizon=3000 if args.network == 'loop' else 1400,
        additional_params=additional_env_params,
    ),

    "network": network,

    "env_state_params": None,
    "group_agents_params": None,
    "multi_agent_config_params": None,
    "agents_params": {
        "name": "all_the_same_vehicles_agents",
        "params": {
            "global_reward": False,
            "default_policy": None,

            "action_params": {
                "name": "VehActionConnector",
                "params": {},
            },
            "obs_params": {
                "name": "CarFollowingConnector",
                "params": {}
            },
            "reward_params": {
                "name": "VehRewardConnector",
                "params": {
                    "W1": [1, 1],
                    "W2": [1, 1],
                    "W3": [1, 1],
                    "W4": [1, 1]
                }
            }
        }
    }
}
# env = WolfEnv.create_env(
#     cls=CarFollowingEnv,
#     network=network,
#     env_params=env_config['env_params'],
#     sim_params=env_config['sim_params'],
#     agents_params=env_config["agents_params"],
#     env_state_params=env_config["env_state_params"],
#     group_agents_params=env_config["group_agents_params"],
#     multi_agent_config_params=env_config["multi_agent_config_params"],
#     simulator=env_config["simulator"],
#     action_repeat_params=env_config.get("action_repeat_params", None),
# )

env = WolfEnv.create_env(
    cls=CarFollowingEnv,
    network=network,
    env_params=env_config['env_params'],
    sim_params=env_config['sim_params'],
    agents_params=env_config["agents_params"],
    env_state_params=env_config["env_state_params"],
    group_agents_params=env_config["group_agents_params"],
    multi_agent_config_params=env_config["multi_agent_config_params"],
    simulator=env_config["simulator"],
    action_repeat_params=env_config.get("action_repeat_params", None),
)


# Enable the environment recording
env.record_train = False
env.record_eval = True
env.trial_id = None



if __name__ == "__main__":

    logger.info("Using device: {}".format(device))

    random.seed(0)
    np.random.seed(0)

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    checkpoint_dir = args.save_dir


    # agent = DDPG(gamma,
    #              tau,
    #              hidden_size,
    #              env.observation_space.shape[0],
    #              env.action_space,
    #              checkpoint_dir=checkpoint_dir
    #              )
    agent = DDPG(gamma,
                    tau,
                    hidden_size,
                    5, ##state space
                    np.array([1,]), ##action space
                    checkpoint_dir=checkpoint_dir
                    )
    if args.controller == 'rl':
        agent.load_checkpoint(args.load_model, training=False)

    # Load the agents parameters
    agent.set_eval()
    # controller='DDPG'


    for _ in range(args.episodes):
        step = 0
        returns = list()
        states=[]
        actions=[]
        lv_speed=[]
        lv_acc=[]
        state=env.reset()
        agent_names = list(state.keys())
        batch_state = np.array([state[agent_name] for agent_name in agent_names])
        # print('state',state)
        if args.controller == 'rl':
            state = torch.Tensor(batch_state).to(device)
        episode_return = 0
        Travel_distance=0
        delta_T=0.1
        while True:
            if args.controller == 'rl':
                states.append(state.cpu().numpy())

                Travel_distance=Travel_distance+state[0][0]*delta_T
                # state[0][-1]=20
                # print('speed',state[0][0], 'speed limit', state[0][3])
                # print("State:", state)
                action = agent.calc_action(state, action_noise=None)
                # print("Action:", action)
                # print('action',type(action),action)
                actions.append(action)
                # if state[0][2]<20 and action>0:
                #     action=-2*action  ##take the brake if too close!!

                accels = action.cpu().numpy()
                if args.constrain:
                    accels = batch_constrain_action(state, accels)
                
                action_dir = dict((agent_name, accel) for agent_name, accel in zip(agent_names, accels))

                next_state, reward, done_dict, _ = env.step(action_dir)
                reward=reward.get('veh_followerstopper_0')
                done=done_dict.get('__all__')
                episode_return += reward
                if done:
                    next_state = None
                else:
                    agent_names = list(next_state.keys())
                    for agent_name in done_dict:
                        if done_dict[agent_name] and agent_name != '__all__':
                            agent_names.remove(agent_name)
                    batch_state = np.array([next_state[agent_name] for agent_name in agent_names])
                    state = torch.Tensor(batch_state).to(device)


                record = env.history_record ## record info
                # human_ino=record.get('human_0')
                # print('info',human_ino['speed'])
                # lv_speed.append(human_ino['speed'])
                # lv_acc.append(human_ino['accel'])
                # np.save('lv_speed.npy',lv_speed)
                # np.save('lv_acc.npy',lv_acc)
                print('travel distance',Travel_distance.cpu().numpy())
            else:
                next_state, reward, done, _ = env.step({})
                done = done.get('__all__')

            step += 1
            if done:
                logger.info(episode_return)
                returns.append(episode_return)
                break

        env_record = env.history_record
        env_global_metric = env.global_metric

        # Convert it to pandas DataFrame
        stats = pd.DataFrame(env_record)

        # Some annotation for filename
        save_dir = os.path.dirname(args.load_model)
        model_name = os.path.basename(args.load_model)
        curr_steps = ''
        if args.controller == 'rl':
            # curr_steps = '_'+''.join(filter(str.isdigit, model_name))
            step_num = re.findall(r'\d+', model_name)
            curr_steps = '_' + '_'.join(step_num)
        lv_str = 'lv' if args.lv else 'no-lv'
        speed_limit_str = args.speed_limit if args.speed_limit else 'dynamic'
        constraint_str = 'constrain' if args.constrain else 'no-constrain'
        with open(os.path.join(save_dir, 
            f'ddpg_test{curr_steps}_{args.controller}_{lv_str}_{args.network}_speed_{speed_limit_str}_{constraint_str}.pkl'), 'wb') as f:
            pkl.dump(stats, f)
        with open(os.path.join(save_dir, 
            f'ddpg_test{curr_steps}_{args.controller}_{lv_str}_{args.network}_speed_{speed_limit_str}_{constraint_str}_global_metric.pkl'), 'wb') as f:
            pkl.dump(env_global_metric, f)

        # np.save('stateidmenv2.npy',states)
        # np.save('actionidmenv2.npy',actions)
    mean = np.mean(returns)
    variance = np.var(returns)
    logger.info("Score (on 100 episodes): {} +/- {}".format(mean, variance))