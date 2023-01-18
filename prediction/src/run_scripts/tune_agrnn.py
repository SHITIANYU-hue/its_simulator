import sys
import time
import datetime

sys.path.insert(0, r'F:\PhD\OneDrive - University of Toronto\PhD\Projects\sow45_code\prediction')
from src.metrics import *
from src.validator import Validator
from src.prediction_models.neural_nets.utils import convert_pems_to_traffic_data
from src.prediction_models.neural_nets.rnn.agrnn import AGRNNPredictor

start = time.time()

path_pems = r'F:\PhD\OneDrive - University of Toronto\data\pems\PEMS04\PEMS04.npz'
pems_csv = r'F:\PhD\OneDrive - University of Toronto\data\pems\PEMS04\PEMS04.csv'
traffic_data = convert_pems_to_traffic_data(path_pems, pems_csv)
traffic_data.split(val_pct=0.2, test_pct=0.2)
traffic_data.set_normalize('standard')
traffic_data.transform()

param_dict = {'seq_len' : [12], 'predictor_method' : ['separate', 'rolling'], 'hidden_size' : [32, 64, 128],
              'alpha' : [0.2, 0.4], 'batch_size': [64, 32, 128],
              'learning_rate' : [1e-3, 1e-4, 1e-5], 'weight_decay' : [1e-3, 1e-4, 1e-5],
              'horizon': [12], 'max_epoch' : [64], 'source_feature':[['flow']],
              'target_feature':[['flow']], 'is_pems':[True]}


validator = Validator(AGRNNPredictor, traffic_data, param_dict)
params, model = validator.tune_model(return_best_model=True)
print('best params: ' + str(params))

mae_errors = {'train':[], 'val':[], 'test':[]}
mse_errors = {'train':[], 'val':[], 'test':[]}
mape_errors = {'train':[], 'val':[], 'test':[]}

def compute_errors(true, predicted):
    metric = MAEMetrics(true, predicted)
    mae_error = metric.evaluate()

    metric = MSEMetrics(true, predicted)
    mse_error = metric.evaluate()

    metric = MAPEMetrics(true, predicted)
    mape_error = 100.*metric.evaluate() # hundred percent
    return mae_error, mse_error, mape_error

true, predicted = model.evaluate('train')
mae_error_train, mse_error_train, mape_error_train = compute_errors(true, predicted)
mae_errors['train'].append(mae_error_train)
mse_errors['train'].append(mse_error_train)
mape_errors['train'].append(mape_error_train)

true, predicted = model.evaluate('val')
mae_error_val, mse_error_val, mape_error_val = compute_errors(true, predicted)
mae_errors['val'].append(mae_error_val)
mse_errors['val'].append(mse_error_val)
mape_errors['val'].append(mape_error_val)

true, predicted = model.evaluate('test')
mae_error_test, mse_error_test, mape_error_test = compute_errors(true, predicted)
mae_errors['test'].append(mae_error_test)
mse_errors['test'].append(mse_error_test)
mape_errors['test'].append(mape_error_test)

print('MAE test:', mae_error_test)
print('MSE test:', mse_error_test)
print('MAPE test:', mape_error_test)
print('training time:', time.time() - start )

today = datetime.datetime.now().strftime('%Y-%m-%d')
model.save_pytorch_model('./{}-{}-e64.pth'.format(today, str(model)) )
