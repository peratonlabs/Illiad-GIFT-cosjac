import os
import torch
import numpy as np
import csv
import json
from torch.nn import Sequential
from copy import deepcopy

import shap
from utils.drebinnn import DrebinNN

# from utils.models import load_model, load_models_dirpath, ImageACModel, ResNetACModel
# from sklearn.ensemble import RandomForestRegressor

# from utils.abstract import AbstractDetector
#from utils.model_utils import compute_action_from_trojai_rl_model
#from utils.models import load_model, load_models_dirpath, ImageACModel, ResNetACModel


#from utils.world import RandomLavaWorldEnv
#from utils.wrappers import ObsEnvWrapper, TensorWrapper


def read_truthfile(truth_fn):
    with open(truth_fn) as f:
        truth = json.load(f)
    lc_truth = {k.lower(): v for k,v in truth.items()}
    return lc_truth

def read_number_from_csv(file_path):
    try:
        with open(file_path, 'r') as csvfile:
            reader = csv.reader(csvfile)
            # Read the first row, which should contain the number
            for row in reader:
                number = int(row[0])  
                return number
    except FileNotFoundError:
        print(f"File not found: {file_path}")
    except IndexError:
        print(f"Invalid file format or empty file: {file_path}")
    except ValueError:
        print(f"Unable to convert the value to a number in file: {file_path}")

def cossim(v1,v2, eps=1e-8):
    #print(v1, v2)

    v1 = v1/(np.linalg.norm(v1) + eps)
    v2 = v2/(np.linalg.norm(v2) + eps)
    
    sim = (v1*v2).sum()

    return sim

def avgcosim(v1, v2, eps=1e-8):
    dim1, _, _= v1.shape
    avgcosim_val = 0
    for inx in range(dim1):
        temp1 = v1[inx,:,:]/(np.linalg.norm(v1[inx,:,:]) + eps)
        temp2 = v2[inx,:,:]/(np.linalg.norm(v2[inx,:,:]) + eps)
        avgcosim_val += (temp1*temp2).sum()
    return avgcosim_val/dim1




def get_class_r14(truth_fn):
    cls = read_number_from_csv(truth_fn)
    return cls


def get_quants(x, n):
    """
    :param x:
    :param n:
    :return:
    """
    q = np.linspace(0, 1, n)
    return np.quantile(x, q)

def get_shapley_values(model: DrebinNN, 
                       dataset: list, 
                       test_dataset: list) -> np.ndarray:
    '''
    Approximate Shapley values for the deep neural network model using the 
    backround dataset
    Input: model - deep neural network model
           dataset - backround dataset
           test_dataset - shapley values will be computed for the test dataset
    Output: an np.ndarray with shapley values
    '''
    shap.initjs()
    explainer_model = shap.GradientExplainer(model, dataset)
    shap_values_model = explainer_model.shap_values(test_dataset)
    return np.stack(shap_values_model, axis=2)

def get_discrete_derivatives(model: DrebinNN, dataset: torch, dataset_pert: torch) -> np.array:
    ''' Calculate discrete derivatives (gradients) by substracting the 
        model outputs using dataset and dataset_pert.
        Args: model - DrebinNN deep neural network model
              dataset - the actual input used to calculate discrete derivatives
              dataset_pert - the input derived from the dataset required for discrete gradients
        Output: discrete derivatives
    '''
            
    output_ref = model.model(dataset)
    output_perturbed_ref = model.model(dataset_pert)
    dim1 = output_ref.shape[0]
    dim2 = dataset.shape[1]
            
    discrete_derivatives = []
    for i in range(dim1):
        discrete_derivatives.append(output_ref[i,:] - output_perturbed_ref[i*dim2:(i+1)*dim2])
    features = torch.stack(discrete_derivatives, dim=0)
    return features.cpu().detach().numpy()

def get_jac(model: DrebinNN, obs ):

    obs.requires_grad = True
    output = model(obs)
    jacobian = []
    y_onehot = torch.zeros([obs.shape[0], output.shape[1]], dtype=torch.long).to(obs.device)
    one = torch.ones([1], dtype=torch.long)
    for label in range(output.shape[1]):
        y_onehot.zero_()
        y_onehot[:, label] = one
        # curr_y_onehot = torch.reshape(y_onehot, out_shape)
        output.backward(y_onehot, retain_graph=True)
        jacobian.append(deepcopy(obs.grad.detach().cpu().numpy()))
        obs.grad.data.zero_()
    
    
    jac = np.stack(jacobian, axis=2)
    #avg_jac = np.mean(np.stack(jacobian, axis=0), axis=(1,2,3,4))
    return jac

def get_scaled_model_output(model: DrebinNN, dataset: torch) -> np.ndarray:
    ''' Get model output and scaled between 0 and 1
        Args:
            model - deep learning mapping
            dataset - model inputs of size (no_samples, no_features)
        Output:
            Scaled model output 
        
    '''
    output_ref = model.model(dataset)
    output_ref = output_ref.cpu().detach().numpy()
    return (output_ref - output_ref.min())/(output_ref.max() - output_ref.min())

def get_env_data(model, obs, env):
    done = False
    max_iters = 1000
    iters = 0
    reward = 0
    collect_jac = []
    while not done and iters < max_iters:
        #print(iters)
        jac = get_jac(model, obs)
        #print(jac.mean())
        collect_jac.append(jac)
        # print(f"Avg jac {jac}")
        env.render()
        action = compute_action_from_trojai_rl_model(model, obs, sample=True)
        # breakpoint()
        obs, reward, terminated, truncated, info = env.step(action)
        # print(obs,env)
        iters += 1
        
        done = terminated or truncated
    collect_jac = np.stack(collect_jac)
    mean_jac = collect_jac.mean(axis=0)
    # print(mean_jac)
    return mean_jac


def dissimilarity_detector(vectorFixed, Newvector):
    # Calculate Euclidean distance
    distance = np.linalg.norm(vectorFixed - Newvector)
    # Normalize the distance to [0, 1] range
    dissimilarity_score = distance / np.sqrt(len(vectorFixed))
    return dissimilarity_score


def get_grad_score(model_filepath,basepath):
    reference_model_path = os.path.join(basepath, "reference", "base_model", "model.pt")
    # load the model
    model, model_repr, model_class = load_model(model_filepath)
    # load ref model
    ref_model, ref_model_repr, ref_model_class = load_model(reference_model_path)
    # Load the config file
    config_dict = {}
    model_dirpath = os.path.dirname(model_filepath)
    #config_filepath = os.path.join(model_dirpath, 'config.json')
    config_filepath = os.path.join(model_dirpath, 'reduced-config.json')

    with open(config_filepath) as config_file:
        config_dict = json.load(config_file)
    
    size = config_dict["grid_size"]
    #print(size)

    # device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    # model.to(device)
    model.eval()
    ref_model.eval()
    
    #model.train()
    #ref_model.train()

    # logging.info("Using compute device: {}".format(device))

    model_name = type(model).__name__
    observation_mode = "rgb" if model_name in [ImageACModel.__name__, ResNetACModel.__name__] else 'simple'

    wrapper_obs_mode = 'simple_rgb' if observation_mode == 'rgb' else 'simple'

    env = TensorWrapper(ObsEnvWrapper(RandomLavaWorldEnv(mode=observation_mode, grid_size=size), mode=wrapper_obs_mode))
    
    #obs, info = env.reset()
    #new_feat, ref_feat, sim = run_ep_2x(model, ref_model, obs, env)
    
    
    sims=[]
    
    for i in range(100):
    	obs, info = env.reset()
    	_,_,sim=run_ep_2x(model, ref_model, obs, env)
    	sims.append(sim)
    
    sim = np.mean(sims)
    
    

    #ref_feat = get_env_data(ref_model, obs, env)
    #obs, info = env.reset()
    #new_feat = get_env_data(model, obs, env)
    
    
    
    #feats_score = dissimilarity_detector(vectorFixed=ref_feat, Newvector=new_feat)
    #return feats_score
    return sim
    
    
    
def run_ep_2x(model1, model2, obs, env):
    done = False
    max_iters = 1000
    iters = 0
    reward = 0
    collect_jac1 = []
    collect_jac2 = []
    sims = [0]
    
    while not done and iters < max_iters:
        #print(iters)
        
        jac1 = get_jac(model1, obs)
        jac2 = get_jac(model2, obs)
        
        
        sim = cossim(jac1,jac2) #~0.78 AUC
        
        #print(jac1, jac2)
        
        #jac = get_jac(model, obs)
        #jac = get_jac(model, obs)
        
        collect_jac1.append(jac1)
        collect_jac2.append(jac2)
        

        
        
        logits1 = model1(obs)
        logits2 = model2(obs)
        
        #print(logits1.shape)
        probs1 = torch.softmax(logits1,1)
        probs2 = torch.softmax(logits2,1)
        
        sim_logits = cossim(logits1.detach().cpu().numpy(),logits2.detach().cpu().numpy())
        sim_probs = cossim(probs1.detach().cpu().numpy(),probs2.detach().cpu().numpy())
        
        
        #sim=sim_logits + sim
        sim=sim_logits    #     0.4192841490138787 AUC
        #sim=sim_probs    #     0.351990504017531 AUC
        
        if ~np.isnan(sim):
            sims.append(sim)
        
        
        action = compute_action_from_trojai_rl_model(model1, obs, sample=True)
        
        

        # print(f"Avg jac {jac}")
        #env.render()
        action = compute_action_from_trojai_rl_model(model1, obs, sample=True)
        # breakpoint()
        obs, reward, terminated, truncated, info = env.step(action)
        # print(obs,env)
        iters += 1
        done = terminated or truncated
    collect_jac1 = np.stack(collect_jac1)
    collect_jac2 = np.stack(collect_jac2)
    mean_jac1 = collect_jac1.mean(axis=0)
    mean_jac2 = collect_jac2.mean(axis=0)
    mean_sim = np.mean(sims)
    # print(mean_jac)
    return mean_jac1, mean_jac2, mean_sim
    
    
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score

def verify_binary_classifier(predictions, labels):
    """
    Function to compute precision, recall, F1 score, and ROC-AUC for a binary classifier.
    
    Args:
    predictions (list or array): Predicted probabilities or binary predictions.
    labels (list or array): True binary labels.
    
    Returns:
    dict: A dictionary containing precision, recall, F1 score, and ROC-AUC.
    """
    # Convert predictions to binary format (if they are not already binary)
    binary_predictions = [1 if p >= 0.5 else 0 for p in predictions]

    # Calculate precision, recall, and F1 score
    precision = precision_score(labels, binary_predictions)
    recall = recall_score(labels, binary_predictions)
    f1 = f1_score(labels, binary_predictions)

    # Calculate ROC-AUC
    # Note: roc_auc_score can handle both binary and continuous predictions
    roc_auc = roc_auc_score(labels, predictions)

    # Compile and return the results
    results = {
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "roc_auc": roc_auc
    }

    return results

def get_adversarial_examples(model, X, eps):

    jacobian =  get_jac(model.model, X )
    signjac = jacobian/np.abs(jacobian)
    signjac = torch.from_numpy(signjac).float().to(model.device)
    X_modified_pc0 = X + eps*signjac[:,:,0]
    X_modified_pc1 = X + eps*signjac[:,:,1]        
            
    output = model.model(X)      
    output_pc0 = model.model(X_modified_pc0)
    output_pc1 = model.model(X_modified_pc1) 

    index_adversarial_examples_class01_pc0 = []
    index_adversarial_examples_class10_pc0 = []
    index_adversarial_examples_class01_pc1 = []
    index_adversarial_examples_class10_pc1 = []
    size, _ = output.shape

    for inx in range(size):
        s1, s2 = output[inx,0], output[inx,1] 
        if s1 < s2 and output_pc0[inx,0] > output_pc0[inx,1]:  
            index_adversarial_examples_class10_pc0.append(inx)
        elif s1 > s2 and output_pc0[inx,0] < output_pc0[inx,1]:
            index_adversarial_examples_class01_pc0.append(inx)
        else:
            pass

        if s1 < s2 and output_pc1[inx,0] > output_pc1[inx,1]:
            index_adversarial_examples_class10_pc1.append(inx)
        elif s1 > s2 and output_pc1[inx,0] < output_pc1[inx,1]:
            index_adversarial_examples_class01_pc1.append(inx)
        else:
             pass        

        list_index_adv_examples = [index_adversarial_examples_class10_pc0, index_adversarial_examples_class01_pc0, 
                      index_adversarial_examples_class10_pc1, index_adversarial_examples_class01_pc1]
        print("Len of index_adversarial_examples_class10_class01_pc0:", len(index_adversarial_examples_class10_pc0), len(index_adversarial_examples_class01_pc0))
        print("Len of index_adversarial_examples_class10_class01_pc1:", len(index_adversarial_examples_class10_pc1), len(index_adversarial_examples_class01_pc1))
        list_modified_datasets =  [X_modified_pc0, X_modified_pc0, X_modified_pc1, X_modified_pc1]
        list_adversarial_examples = []
        for inx, list_index in enumerate(list_index_adv_examples):
            if len(list_index) > 0:
                list_adversarial_examples.append(list_modified_datasets[inx][list_index,:].cpu().detach().numpy())
        if list_adversarial_examples:
            return np.concatenate(list_adversarial_examples) if len(list_adversarial_examples) > 1 else list_adversarial_examples[0]
        else:
            return None

def get_no_labels_class(Y):
    count = sum(1 for label in Y if label == 0)
    return count, len(Y) - count

def get_prediction_class_samples(output, no_samples):
    count0, count1 = 0, 0
    for inx in range(no_samples):
        p0, p1 = output[inx]
        count0, count1 = (count0 + 1, count1) if p0 > p1 else (count0, count1 + 1)
    return count0, count1

def apply_binomial_pert_dataset(dataset: np.array, n: int, p: float, mode: str) -> np.array:
    '''
        Expends dataset by n times and applies binnary perturbations
        Args:
            dataset - input samples
            n - dataset will be expended n times
            p - binomial distribution probability parameter
        Output:
            Pertubed and expended dataset  
    '''
    if mode == 'drebinn' and n >= 6:
        msg = "Can not expend drebbin dataset more than 6 times"
        raise Exception(msg)
        
    dataset = np.repeat(dataset, n, axis=0)
    flips = np.random.binomial(1, p, size=dataset.shape)                
    dataset[flips == 1] = 1 - dataset[flips == 1]
    return dataset

def get_discrete_derivative_inputs(dataset: np.ndarray) -> np.array:
    '''
    Derive new samples required for the calculation of the discrete gradients. 
    For each original sample in the initial dataset, a set of samples are derived by 
    modifying the original sample features one at a time and preserving the boolean
    type.  
    Args:
        dataset - input samples dataset of size (no_samples, no_features)
    Output:
        Discrete gradient dataset
    '''
    list_new_vectors = []
    for i in range(dataset.shape[0]):
        list_new_vectors.append(np.abs(np.eye(dataset.shape[1]) - dataset[i,:]))
    return np.concatenate(list_new_vectors, axis=0)

def scale_probability(outcome: float, method: str) -> str:
    '''Transform outcompe into probability
    Args:
        outcome - a float number
        method - cosine similarity returns outcome between (-1,1) 
                 jensen-shannon returns a probability (0,1)
    Output:
        The outcome is scaled between 0 and 1 and converted to string.
    '''
    if method != 'jensen-shannon':
        probability = 0.5*(1-outcome) 
    probability = np.clip(probability, 0, 1)
    return str(probability)


def fast_gradient_sign_method(dataset: torch, jacobian: torch, device: str, eps: float):
    '''
    Get potential adversarial examples for a classification model
    whose jacobian with respect to the dataset is provided    
    Args:
        dataset - input of size (no_samples, no_features)
        jacobian - jacobian of a model with respect to the input dataset
        device - it could be CPU or GPU
        eps - magnitude of the gradient 
    '''
    signjac = jacobian/np.abs(jacobian)
    signjac = torch.from_numpy(signjac).float().to(device)
    return dataset + eps*signjac[:, :, 0], dataset + eps*signjac[:, :, 1]     


def identify_adversarial_examples(
    model: DrebinNN,
    dataset: torch,
    adataset: torch
):
    '''
    Identify adversarial examples for a classification model
    inside ppdataset using original input dataset
    Args:
        model: a pytorch classifier
        dataset: original set of samples used to get adversarial
                 examples
        adataset: potential adversarial dataset
    Output:
        two list of indices of adversarial examples
    '''
    output = model.model(dataset)
    output_pc0 = model.model(adataset)

    index_adv_examples_class01 = []
    index_adv_examples_class10 = []
    size, _ = output.shape

    for inx in range(size):
        s1, s2 = output[inx, 0], output[inx, 1]
        if s1 < s2 and output_pc0[inx, 0] > output_pc0[inx, 1]:
            index_adv_examples_class01.append(inx)
        elif s1 > s2 and output_pc0[inx, 0] < output_pc0[inx, 1]:
            index_adv_examples_class10.append(inx)
        else:
            pass
    return index_adv_examples_class01, index_adv_examples_class10
