import argparse
import logging
import time
import collections
import os.path
from tqdm import tqdm
import torch.utils.data as data
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torch


import datatools.set_simp
import datatools.set_polarity
import datatools.sequence_classification
import datatools.word_vectors
import modules.maxpool_lstm
import modules.kim_cnn
import monitoring.reporting
import monitoring.tb_log
def add_args(parser):
    if parser is None:
        parser= argparse.ArgumentParser() 
    parser.add_argument("--dataset_for_classification",type=str,choices=["simple","moviepol"],default="simple")

    parser.add_argument("--ds_path", type=str,default=None)
    parser.add_argument("--fasttext_path", type=str,default="../data/fastText_word_vectors/" )
    parser.add_argument("--data_trim", type=int, default=30000)
    parser.add_argument("--lstm_hidden_dim", type = int, default =300)
    parser.add_argument("--maxlstm_dropout_rate", type = int, default = 0.5)
    parser.add_argument("--reports_per_epoch", type=int,default=10)
    parser.add_argument("--save_prefix", type=str,default=None)
    parser.add_argument("--model_type", type=str, choices=["maxpool_lstm_fc", "kimcnn"],default="maxpool_lstm_fc")
    modules.kim_cnn.add_args(parser)
    
    return parser

Context=collections.namedtuple("Context","model, train_loader, val_loader, optimizer, indexer, category_names, tb_writer")

def make_context(args):
   if args.dataset_for_classification == "simple":
        if args.save_prefix is None:
            args.save_prefix="simplification_classification"
        if args.ds_path is None:
            args.ds_path= "../data/sentence-aligned.v2" 
        train_dataset, val_dataset, index2vec, indexer = datatools.set_simp.load(args)
        category_names={0:"normal",1:"simple"}
   elif args.dataset_for_classification == "moviepol":
        if args.save_prefix is  None:
            args.save_prefix= "moviepol"
        if args.ds_path is None:
            args.dspath = "../data/rt-polaritydata"
        train_dataset, val_dataset, index2vec, indexer = datatools.set_polarity.load(args)
        category_names={0:"negative",1:"positive"}
   else:
        raise Exception("Unknown dataset")
   

   train_loader= data.DataLoader(train_dataset,batch_size = args.batch_size,shuffle = True,collate_fn = datatools.sequence_classification.make_collater(args))
   logging.info("Length of train set :"+ str(len(train_dataset))+". Length of data loader: "+str(len(train_loader))+"." )
   val_loader= data.DataLoader(val_dataset,batch_size = args.batch_size, shuffle = False, collate_fn = datatools.sequence_classification.make_collater(args))
   embedding=datatools.word_vectors.embedding(index2vec, indexer.n_words,300)
   if args.model_type == "maxpool_lstm_fc":
    model=modules.maxpool_lstm.MaxPoolLSTMFC.from_args(embedding, args) 
   elif args.model_type == "kimcnn":
       model=modules.kim_cnn.KimCNN.from_args(embedding,args) 
   if args.cuda:
       model=model.cuda()
   optimizer=optim.SGD(model.parameters(),lr=0.01)
   return Context(model, train_loader, val_loader, optimizer, indexer,category_names=category_names, tb_writer=monitoring.tb_log.TBWriter())






def run(args):

   context=make_context(args) 
   context.tb_writer.write_hyperparams()
   starttime=time.time()
   timestamp=monitoring.reporting.timestamp()
   
   report_interval=max(len(context.train_loader) //  args.reports_per_epoch ,1)
   eval_score=float("inf")
   accumulated_loss=0 
   if args.resume:
       context.model.load(os.path.join(args.model_save_path,args.res_file))

   for epoch_count in range(args.num_epochs):
        logging.info("Starting epoch "+str(epoch_count) +".")
  
        step=0
        epoch_start_time=time.time()
        for seqs, categories, pad_mat, _ in context.train_loader:
            step+=1
            context.optimizer.zero_grad()
            scores=context.model(seqs,pad_mat) #should have dimension batchsize
            loss=F.cross_entropy(scores,categories) 
            loss.backward()
            context.optimizer.step()
            accumulated_loss+=loss.data[0]
            context.tb_writer.write_train_loss(loss.data[0])
            if step % report_interval == 0:
                monitoring.reporting.report(starttime,step,len(context.train_loader), accumulated_loss / report_interval)
                accumulated_loss = 0
        epoch_duration = time.time() - epoch_start_time
        context.tb_writer.write_data_per_second(len(context.train_loader)/epoch_duration)
        old_eval_score=eval_score
        eval_score=datatools.sequence_classification.evaluate(context, context.val_loader)
        context.tb_writer.write_accuracy(eval_score)
        logging.info("Finished epoch number "+ str(epoch_count+1) +  " of " +str(args.num_epochs)+".  Accuracy is "+ str(eval_score) +".")
        if eval_score< old_eval_score:
            logging.info("Saving model")
            context.model.save(os.path.join(args.model_save_path,args.save_prefix +"_best_model_" + timestamp)  )
            context.model.save(os.path.join(args.model_save_path,"recent_model" )  )

   logging.info("Loading best model")
   context.model.load(os.path.join(args.model_save_path,args.save_prefix +"_best_model_" + timestamp))
   datatools.sequence_classification.write_evaulation_report(context, context.val_loader,os.path.join(args.report_path,args.save_prefix + timestamp +".txt") , category_names=context.category_names) 
