from dataloader import EvalDataset, TrainDataset, NewBidirectionalOneShotIterator
from dataloader import get_dataset

import argparse
import os
import logging
import time
import json

backend = os.environ.get('DGLBACKEND', 'pytorch')
if backend.lower() == 'mxnet':
    import multiprocessing as mp
    from train_mxnet import load_model
    from train_mxnet import train
    from train_mxnet import test
else:
    import torch.multiprocessing as mp
    from train_pytorch import load_model
    from train_pytorch import train, train_mp
    from train_pytorch import test, test_mp

class ArgParser(argparse.ArgumentParser):
    def __init__(self):
        super(ArgParser, self).__init__()

        self.add_argument('--model_name', default='TransE',
                          choices=['TransE', 'TransE_l1', 'TransE_l2', 'TransR',
                                   'RESCAL', 'DistMult', 'ComplEx', 'RotatE'],
                          help='model to use')
        self.add_argument('--data_path', type=str, default='data',
                          help='root path of all dataset')
        self.add_argument('--dataset', type=str, default='FB15k',
                          help='dataset name, under data_path')
        self.add_argument('--format', type=str, default='built_in',
                          help='the format of the dataset, it can be built_in,'\
                                'raw_udd_{htr} and udd_{htr}')
        self.add_argument('--data_files', type=str, default=None, nargs='+',
                          help='a list of data files, e.g. entity relation train valid test')
        self.add_argument('--save_path', type=str, default='ckpts',
                          help='place to save models and logs')
        self.add_argument('--save_emb', type=str, default=None,
                          help='save the embeddings in the specific location.')

        self.add_argument('--max_step', type=int, default=80000,
                          help='train xx steps')
        self.add_argument('--warm_up_step', type=int, default=None,
                          help='for learning rate decay')
        self.add_argument('--batch_size', type=int, default=1024,
                          help='batch size')
        self.add_argument('--batch_size_eval', type=int, default=8,
                          help='batch size used for eval and test')
        self.add_argument('--neg_sample_size', type=int, default=128,
                          help='negative sampling size')
        self.add_argument('--neg_chunk_size', type=int, default=-1,
                          help='chunk size of the negative edges.')
        self.add_argument('--neg_deg_sample', action='store_true',
                          help='negative sample proportional to vertex degree in the training')
        self.add_argument('--neg_deg_sample_eval', action='store_true',
                          help='negative sampling proportional to vertex degree in the evaluation')
        self.add_argument('--neg_sample_size_valid', type=int, default=1000,
                          help='negative sampling size for validation')
        self.add_argument('--neg_chunk_size_valid', type=int, default=-1,
                          help='chunk size of the negative edges.')
        self.add_argument('--neg_sample_size_test', type=int, default=-1,
                          help='negative sampling size for testing')
        self.add_argument('--neg_chunk_size_test', type=int, default=-1,
                          help='chunk size of the negative edges.')
        self.add_argument('--hidden_dim', type=int, default=256,
                          help='hidden dim used by relation and entity')
        self.add_argument('--lr', type=float, default=0.0001,
                          help='learning rate')
        self.add_argument('-g', '--gamma', type=float, default=12.0,
                          help='margin value')
        self.add_argument('--eval_percent', type=float, default=1,
                          help='sample some percentage for evaluation.')
        self.add_argument('--no_eval_filter', action='store_true',
                          help='do not filter positive edges among negative edges for evaluation')

        self.add_argument('--gpu', type=int, default=[-1], nargs='+', 
                          help='a list of active gpu ids, e.g. 0 1 2 4')
        self.add_argument('--mix_cpu_gpu', action='store_true',
                          help='mix CPU and GPU training')
        self.add_argument('-de', '--double_ent', action='store_true',
                          help='double entitiy dim for complex number')
        self.add_argument('-dr', '--double_rel', action='store_true',
                          help='double relation dim for complex number')
        self.add_argument('--seed', type=int, default=0,
                          help='set random seed fro reproducibility')
        self.add_argument('-log', '--log_interval', type=int, default=1000,
                          help='do evaluation after every x steps')
        self.add_argument('--eval_interval', type=int, default=10000,
                          help='do evaluation after every x steps')
        self.add_argument('-adv', '--neg_adversarial_sampling', action='store_true',
                          help='if use negative adversarial sampling')
        self.add_argument('-a', '--adversarial_temperature', default=1.0, type=float)

        self.add_argument('--valid', action='store_true',
                          help='if valid a model')
        self.add_argument('--test', action='store_true',
                          help='if test a model')
        self.add_argument('-rc', '--regularization_coef', type=float, default=0.000002,
                          help='set value > 0.0 if regularization is used')
        self.add_argument('-rn', '--regularization_norm', type=int, default=3,
                          help='norm used in regularization')
        self.add_argument('--num_worker', type=int, default=32,
                          help='number of workers used for loading data')
        self.add_argument('--non_uni_weight', action='store_true',
                          help='if use uniform weight when computing loss')
        self.add_argument('--init_step', type=int, default=0,
                          help='DONT SET MANUALLY, used for resume')
        self.add_argument('--step', type=int, default=0,
                          help='DONT SET MANUALLY, track current step')
        self.add_argument('--pickle_graph', action='store_true',
                          help='pickle built graph, building a huge graph is slow.')
        self.add_argument('--num_proc', type=int, default=1,
                          help='number of process used')
        self.add_argument('--num_test_proc', type=int, default=1,
                          help='number of process used for test')
        self.add_argument('--num_thread', type=int, default=1,
                          help='number of thread used')
        self.add_argument('--rel_part', action='store_true',
                          help='enable relation partitioning')
        self.add_argument('--soft_rel_part', action='store_true',
                          help='enable soft relation partition')
        self.add_argument('--nomp_thread_per_process', type=int, default=-1,
                          help='num of omp threads used per process in multi-process training')
        self.add_argument('--async_update', action='store_true',
                          help='allow async_update on node embedding')
        self.add_argument('--force_sync_interval', type=int, default=-1,
                          help='We force a synchronization between processes every x steps')


def get_logger(args):
    if not os.path.exists(args.save_path):
        os.mkdir(args.save_path)

    folder = '{}_{}_'.format(args.model_name, args.dataset)
    n = len([x for x in os.listdir(args.save_path) if x.startswith(folder)])
    folder += str(n)
    args.save_path = os.path.join(args.save_path, folder)

    if not os.path.exists(args.save_path):
        os.makedirs(args.save_path)
    log_file = os.path.join(args.save_path, 'train.log')

    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S',
        filename=log_file,
        filemode='w'
    )

    logger = logging.getLogger(__name__)
    print("Logs are being recorded at: {}".format(log_file))
    return logger


def run(args, logger):
    train_time_start = time.time()
    # load dataset and samplers
    dataset = get_dataset(args.data_path, args.dataset, args.format, args.data_files)
    n_entities = dataset.n_entities
    n_relations = dataset.n_relations
    if args.neg_sample_size_test < 0:
        args.neg_sample_size_test = n_entities
    args.eval_filter = not args.no_eval_filter
    if args.neg_deg_sample_eval:
        assert not args.eval_filter, "if negative sampling based on degree, we can't filter positive edges."

    # When we generate a batch of negative edges from a set of positive edges,
    # we first divide the positive edges into chunks and corrupt the edges in a chunk
    # together. By default, the chunk size is equal to the negative sample size.
    # Usually, this works well. But we also allow users to specify the chunk size themselves.
    if args.neg_chunk_size < 0:
        args.neg_chunk_size = args.neg_sample_size
    if args.neg_chunk_size_valid < 0:
        args.neg_chunk_size_valid = args.neg_sample_size_valid
    if args.neg_chunk_size_test < 0:
        args.neg_chunk_size_test = args.neg_sample_size_test

    num_workers = args.num_worker
    train_data = TrainDataset(dataset, args, ranks=args.num_proc)
    # if there is no cross partition relaiton, we fall back to strict_rel_part
    args.strict_rel_part = args.mix_cpu_gpu and (train_data.cross_part == False)
    args.soft_rel_part = args.mix_cpu_gpu and args.soft_rel_part and train_data.cross_part

    # Automatically set number of OMP threads for each process if it is not provided
    # The value for GPU is evaluated in AWS p3.16xlarge
    # The value for CPU is evaluated in AWS x1.32xlarge
    if args.nomp_thread_per_process == -1:
        if len(args.gpu) > 0:
            # GPU training
            args.num_thread = 4
        else:
            # CPU training
            args.num_thread = 1
    else:
        args.num_thread = args.nomp_thread_per_process

    if args.num_proc > 1:
        train_samplers = []
        for i in range(args.num_proc):
            train_sampler_head = train_data.create_sampler(args.batch_size,
                                                           args.neg_sample_size,
                                                           args.neg_chunk_size,
                                                           mode='head',
                                                           num_workers=num_workers,
                                                           shuffle=True,
                                                           exclude_positive=False,
                                                           rank=i)
            train_sampler_tail = train_data.create_sampler(args.batch_size,
                                                           args.neg_sample_size,
                                                           args.neg_chunk_size,
                                                           mode='tail',
                                                           num_workers=num_workers,
                                                           shuffle=True,
                                                           exclude_positive=False,
                                                           rank=i)
            train_samplers.append(NewBidirectionalOneShotIterator(train_sampler_head, train_sampler_tail,
                                                                  args.neg_chunk_size, args.neg_sample_size,
                                                                  True, n_entities))
    else:
        train_sampler_head = train_data.create_sampler(args.batch_size,
                                                       args.neg_sample_size,
                                                       args.neg_chunk_size,
                                                       mode='head',
                                                       num_workers=num_workers,
                                                       shuffle=True,
                                                       exclude_positive=False)
        train_sampler_tail = train_data.create_sampler(args.batch_size,
                                                       args.neg_sample_size,
                                                       args.neg_chunk_size,
                                                       mode='tail',
                                                       num_workers=num_workers,
                                                       shuffle=True,
                                                       exclude_positive=False)
        train_sampler = NewBidirectionalOneShotIterator(train_sampler_head, train_sampler_tail,
                                                        args.neg_chunk_size, args.neg_sample_size,
                                                        True, n_entities)

    # for multiprocessing evaluation, we don't need to sample multiple batches at a time
    # in each process.
    if args.num_proc > 1:
        num_workers = 1
    if args.valid or args.test:
        if len(args.gpu) > 1:
            args.num_test_proc = args.num_proc if args.num_proc < len(args.gpu) else len(args.gpu)
        else:
            args.num_test_proc = args.num_proc
        eval_dataset = EvalDataset(dataset, args)
    if args.valid:
        # Here we want to use the regualr negative sampler because we need to ensure that
        # all positive edges are excluded.
        if args.num_proc > 1:
            valid_sampler_heads = []
            valid_sampler_tails = []
            for i in range(args.num_proc):
                valid_sampler_head = eval_dataset.create_sampler('valid', args.batch_size_eval,
                                                                 args.neg_sample_size_valid,
                                                                 args.neg_chunk_size_valid,
                                                                 args.eval_filter,
                                                                 mode='chunk-head',
                                                                 num_workers=num_workers,
                                                                 rank=i, ranks=args.num_proc)
                valid_sampler_tail = eval_dataset.create_sampler('valid', args.batch_size_eval,
                                                                 args.neg_sample_size_valid,
                                                                 args.neg_chunk_size_valid,
                                                                 args.eval_filter,
                                                                 mode='chunk-tail',
                                                                 num_workers=num_workers,
                                                                 rank=i, ranks=args.num_proc)
                valid_sampler_heads.append(valid_sampler_head)
                valid_sampler_tails.append(valid_sampler_tail)
        else:
            valid_sampler_head = eval_dataset.create_sampler('valid', args.batch_size_eval,
                                                             args.neg_sample_size_valid,
                                                             args.neg_chunk_size_valid,
                                                             args.eval_filter,
                                                             mode='chunk-head',
                                                             num_workers=num_workers,
                                                             rank=0, ranks=1)
            valid_sampler_tail = eval_dataset.create_sampler('valid', args.batch_size_eval,
                                                             args.neg_sample_size_valid,
                                                             args.neg_chunk_size_valid,
                                                             args.eval_filter,
                                                             mode='chunk-tail',
                                                             num_workers=num_workers,
                                                             rank=0, ranks=1)
    if args.test:
        # Here we want to use the regualr negative sampler because we need to ensure that
        # all positive edges are excluded.
        # We use a maximum of num_gpu in test stage to save GPU memory.
        if args.num_test_proc > 1:
            test_sampler_tails = []
            test_sampler_heads = []
            for i in range(args.num_test_proc):
                test_sampler_head = eval_dataset.create_sampler('test', args.batch_size_eval,
                                                                args.neg_sample_size_test,
                                                                args.neg_chunk_size_test,
                                                                args.eval_filter,
                                                                mode='chunk-head',
                                                                num_workers=num_workers,
                                                                rank=i, ranks=args.num_test_proc)
                test_sampler_tail = eval_dataset.create_sampler('test', args.batch_size_eval,
                                                                args.neg_sample_size_test,
                                                                args.neg_chunk_size_test,
                                                                args.eval_filter,
                                                                mode='chunk-tail',
                                                                num_workers=num_workers,
                                                                rank=i, ranks=args.num_test_proc)
                test_sampler_heads.append(test_sampler_head)
                test_sampler_tails.append(test_sampler_tail)
        else:
            test_sampler_head = eval_dataset.create_sampler('test', args.batch_size_eval,
                                                            args.neg_sample_size_test,
                                                            args.neg_chunk_size_test,
                                                            args.eval_filter,
                                                            mode='chunk-head',
                                                            num_workers=num_workers,
                                                            rank=0, ranks=1)
            test_sampler_tail = eval_dataset.create_sampler('test', args.batch_size_eval,
                                                            args.neg_sample_size_test,
                                                            args.neg_chunk_size_test,
                                                            args.eval_filter,
                                                            mode='chunk-tail',
                                                            num_workers=num_workers,
                                                            rank=0, ranks=1)

    # We need to free all memory referenced by dataset.
    eval_dataset = None
    dataset = None
    # load model
    model = load_model(logger, args, n_entities, n_relations)

    if args.num_proc > 1 or args.async_update:
        model.share_memory()

    print('Total data loading time {:.3f} seconds'.format(time.time() - train_time_start))

    # train
    start = time.time()
    rel_parts = train_data.rel_parts if args.strict_rel_part or args.soft_rel_part else None
    cross_rels = train_data.cross_rels if args.soft_rel_part else None
    if args.num_proc > 1:
        procs = []
        barrier = mp.Barrier(args.num_proc)
        for i in range(args.num_proc):
            valid_sampler = [valid_sampler_heads[i], valid_sampler_tails[i]] if args.valid else None
            proc = mp.Process(target=train_mp, args=(args,
                                                     model,
                                                     train_samplers[i],
                                                     valid_sampler,
                                                     i,
                                                     rel_parts,
                                                     cross_rels,
                                                     barrier))
            procs.append(proc)
            proc.start()
        for proc in procs:
            proc.join()
    else:
        valid_samplers = [valid_sampler_head, valid_sampler_tail] if args.valid else None
        train(args, model, train_sampler, valid_samplers, rel_parts=rel_parts)
    print('training takes {} seconds'.format(time.time() - start))

    if args.save_emb is not None:
        if not os.path.exists(args.save_emb):
            os.mkdir(args.save_emb)
        model.save_emb(args.save_emb, args.dataset)

        # We need to save the model configurations as well.
        conf_file = os.path.join(args.save_emb, 'config.json')
        with open(conf_file, 'w') as outfile:
            json.dump({'dataset': args.dataset,
                       'model': args.model_name,
                       'emb_size': args.hidden_dim,
                       'max_train_step': args.max_step,
                       'batch_size': args.batch_size,
                       'neg_sample_size': args.neg_sample_size,
                       'lr': args.lr,
                       'gamma': args.gamma,
                       'double_ent': args.double_ent,
                       'double_rel': args.double_rel,
                       'neg_adversarial_sampling': args.neg_adversarial_sampling,
                       'adversarial_temperature': args.adversarial_temperature,
                       'regularization_coef': args.regularization_coef,
                       'regularization_norm': args.regularization_norm},
                       outfile, indent=4)

    # test
    if args.test:
        start = time.time()
        if args.num_test_proc > 1:
            queue = mp.Queue(args.num_test_proc)
            procs = []
            for i in range(args.num_test_proc):
                proc = mp.Process(target=test_mp, args=(args,
                                                        model,
                                                        [test_sampler_heads[i], test_sampler_tails[i]],
                                                        i,
                                                        'Test',
                                                        queue))
                procs.append(proc)
                proc.start()

            total_metrics = {}
            metrics = {}
            logs = []
            for i in range(args.num_test_proc):
                log = queue.get()
                logs = logs + log
            
            for metric in logs[0].keys():
                metrics[metric] = sum([log[metric] for log in logs]) / len(logs)
            for k, v in metrics.items():
                print('Test average {} at [{}/{}]: {}'.format(k, args.step, args.max_step, v))

            for proc in procs:
                proc.join()
        else:
            test(args, model, [test_sampler_head, test_sampler_tail])
        print('test:', time.time() - start)

if __name__ == '__main__':
    args = ArgParser().parse_args()
    logger = get_logger(args)
    run(args, logger)
