import torch
import torch.nn as nn
import torch.nn.functional as F

from typing import Tuple,List
from .conv import conv_layer
from .resblock import ResBlock
from .detection import DetectionHead

from utils.utils import random_sample_selection

import math
import numpy as np
from cv2 import cv2

class LFFDTinyPart(nn.Module):
    # these configs are for 2 headed lffd-tiny detector
    __FILTERS__ = [64,64]
    __RF_SIZES__ = [55,71]
    __RF_STRIDES__ = [4,4]
    __SCALES__ = [(10,15),(15,20)] # calculated for 640 image input

    def __init__(self, in_channels:int=3, filters:List[int]=None,
            rf_sizes:List[int]=None, rf_strides:List[int]=None, scales:List[int]=None,
            num_classes:int=1):
        super(LFFDTinyPart,self).__init__()

        if filters is None: filters = LFFDTinyPart.__FILTERS__
        if rf_sizes is None: rf_sizes = LFFDTinyPart.__RF_SIZES__
        if rf_strides is None: rf_strides = LFFDTinyPart.__RF_STRIDES__
        if scales is None: scales = LFFDTinyPart.__SCALES__
        self.num_classes = num_classes

        self.downsample_conv1 = conv_layer(in_channels,64,stride=2,padding=1)
        self.downsample_conv2 = conv_layer(64,64,stride=2,padding=1)
        self.res_block1 = ResBlock(64)
        self.res_block2 = ResBlock(64)
        self.res_block3 = ResBlock(64)
        self.res_block4 = ResBlock(64)

        self.heads = nn.ModuleList([
            DetectionHead(idx+1,num_of_filters,rf_size,rf_stride,lower_scale,upper_scale,
                num_classes=num_classes)
            for idx,(num_of_filters, rf_size, rf_stride, (lower_scale,upper_scale)) in enumerate(zip(filters,rf_sizes,rf_strides,scales))
        ])

    def forward(self, x:torch.Tensor) -> Tuple[List[torch.Tensor], List[torch.Tensor]]:
        logits:List = []
        # *tiny part
        c1 = self.downsample_conv1(x)
        c2 = self.downsample_conv2(c1)
        c4 = self.res_block1(c2)
        c6 = self.res_block2(c4)
        c8 = self.res_block3(c6)
        logits.append(c8)

        c10 = self.res_block4(c8)
        logits.append(c10)

        cls_logits:List = []
        reg_logits:List = []

        for i in range(len(logits)):
            cls_l,reg_l = self.heads[i](logits[i])
            cls_logits.append(cls_l)
            reg_logits.append(reg_l)

        return cls_logits,reg_logits

    def training_step(self, batch:Tuple[torch.Tensor, List[torch.Tensor]], batch_idx:int):

        imgs,gt_boxes = batch

        device = imgs.device
        dtype = imgs.dtype
        batch_size = imgs.size(0)
        ratio = 10

        cls_logits,reg_logits = self(imgs)

        target_cls:List = []
        target_regs:List = []

        target_objectness_mask:List = []
        target_reg_mask:List = []
        ignore:List = []

        for i in range(len(self.heads)):
            # *for each head
            fh,fw = cls_logits[i].shape[2:]

            t_cls,t_regs,ig = self.heads[i].build_targets((fh,fw), gt_boxes, device=device, dtype=dtype)
            # t_cls          : bs,fh',fw'      | type: model.dtype          | device: model.device
            # t_regs         : bs,fh',fw',4    | type: model.dtype          | device: model.device
            # ig             : bs,fh',fw'      | type: torch.bool           | device: model.device

            cls_logits[i] = cls_logits[i].permute(0,2,3,1).view(batch_size, -1)
            reg_logits[i] = reg_logits[i].permute(0,2,3,1).view(batch_size, -1, 4)

            target_cls.append(t_cls.view(batch_size, -1))
            target_regs.append(t_regs.view(batch_size,-1,4))

            ignore.append(ig.view(batch_size,-1))

        cls_logits = torch.cat(cls_logits, dim=1)
        reg_logits = torch.cat(reg_logits, dim=1)
        target_cls = torch.cat(target_cls, dim=1)
        target_regs = torch.cat(target_regs, dim=1)
        ignore = torch.cat(ignore, dim=1)

        pos_mask = (target_cls == 1) & (~ignore)
        neg_mask = (target_cls == 0) & (~ignore)

        positives = pos_mask.sum()
        negatives = neg_mask.sum()
        negatives = min(negatives,ratio*positives)

        ss, = torch.where(neg_mask.view(-1))
        ss = random_sample_selection(ss.cpu().numpy().tolist(), negatives)
        neg_mask[:] = False
        neg_mask = neg_mask.view(-1)
        neg_mask[ss] = True
        neg_mask = neg_mask.view(batch_size,-1)
        negatives = neg_mask.sum()

        cls_loss = F.binary_cross_entropy_with_logits(
            cls_logits[pos_mask | neg_mask], target_cls[pos_mask | neg_mask])

        reg_loss = F.mse_loss(reg_logits[pos_mask, :], target_regs[pos_mask, :])

        assert not torch.isnan(reg_loss) and not torch.isnan(cls_loss)

        return cls_loss + reg_loss