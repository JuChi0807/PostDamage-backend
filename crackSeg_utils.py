import json
from datetime import datetime
from pathlib import Path

import random
import numpy as np

import torch
import tqdm
from crackSeg_unet.unet_transfer import UNet16, UNetResNet


class AverageMeter(object):
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

def cuda(x):
    return x.cuda(non_blocking=True) if torch.cuda.is_available() else x

def write_event(log, step, **data):
    data['step'] = step
    data['dt'] = datetime.now().isoformat()
    log.write(json.dumps(data, sort_keys=True))
    log.write('\n')
    log.flush()

def check_crop_size(image_height, image_width):
    """Checks if image size divisible by 32.
    Args:
        image_height:
        image_width:
    Returns:
        True if both height and width divisible by 32 and False otherwise.
    """
    return image_height % 32 == 0 and image_width % 32 == 0

def create_model(device, type ='vgg16'):
    assert type == 'vgg16' or type == 'resnet101'
    if type == 'vgg16':
        model = UNet16(pretrained=True)
    elif type == 'resnet101':
        model = UNetResNet(pretrained=True, encoder_depth=101, num_classes=1)
    else:
        assert False
    model.eval()
    return model.to(device)

def load_unet_vgg16(model_path):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    #device = 'cpu'
    model = UNet16(pretrained=True)
    checkpoint = torch.load(model_path,map_location=device)
    if 'model' in checkpoint:
        model.load_state_dict(checkpoint['model'])
    elif 'state_dict' in checkpoint:
        model.load_state_dict(checkpoint['check_point'])
    else:
        raise Exception('undefind model format')

    model = model.to(device)
    model.eval()

    return model

def load_unet_resnet_101(model_path):
    model = UNetResNet(pretrained=True, encoder_depth=101, num_classes=1)
    checkpoint = torch.load(model_path)
    if 'model' in checkpoint:
        model.load_state_dict(checkpoint['model'])
    elif 'state_dict' in checkpoint:
        model.load_state_dict(checkpoint['check_point'])
    else:
        raise Exception('undefind model format')

    model.cuda()
    model.eval()

    return model

def load_unet_resnet_34(model_path):
    model = UNetResNet(pretrained=True, encoder_depth=34, num_classes=1)
    checkpoint = torch.load(model_path)
    if 'model' in checkpoint:
        model.load_state_dict(checkpoint['model'])
    elif 'state_dict' in checkpoint:
        model.load_state_dict(checkpoint['check_point'])
    else:
        raise Exception('undefind model format')

    model.cuda()
    model.eval()

    return model

def train(args, model, criterion, train_loader, valid_loader, validation, init_optimizer, n_epochs=None, fold=None,
          num_classes=None):
    lr = args.lr
    n_epochs = n_epochs or args.n_epochs
    optimizer = init_optimizer(lr)

    root = Path(args.model_path)
    model_path = root / 'model_{fold}.pt'.format(fold=fold)
    if model_path.exists():
        state = torch.load(str(model_path))
        epoch = state['epoch']
        step = state['step']
        model.load_state_dict(state['model'])
        print('Restored model, epoch {}, step {:,}'.format(epoch, step))
    else:
        epoch = 1
        step = 0

    save = lambda ep: torch.save({
        'model': model.state_dict(),
        'epoch': ep,
        'step': step,
    }, str(model_path))

    report_each = 10
    log = root.joinpath('train_{fold}.log'.format(fold=fold)).open('at', encoding='utf8')
    valid_losses = []
    for epoch in range(epoch, n_epochs + 1):
        model.train()
        random.seed()
        tq = tqdm.tqdm(total=(len(train_loader) * args.batch_size))
        tq.set_description('Epoch {}, lr {}'.format(epoch, lr))
        losses = []
        tl = train_loader
        try:
            mean_loss = 0
            for i, (inputs, targets) in enumerate(tl):
                inputs = cuda(inputs)

                with torch.no_grad():
                    targets = cuda(targets)

                outputs = model(inputs)
                #print(outputs.shape, targets.shape)
                loss = criterion(outputs, targets)
                optimizer.zero_grad()
                batch_size = inputs.size(0)
                loss.backward()
                optimizer.step()
                step += 1
                tq.update(batch_size)
                losses.append(loss.item())
                mean_loss = np.mean(losses[-report_each:])
                tq.set_postfix(loss='{:.5f}'.format(mean_loss))
                if i and i % report_each == 0:
                    write_event(log, step, loss=mean_loss)
            write_event(log, step, loss=mean_loss)
            tq.close()
            save(epoch + 1)
            valid_metrics = validation(model, criterion, valid_loader, num_classes)
            write_event(log, step, **valid_metrics)
            valid_loss = valid_metrics['valid_loss']
            valid_losses.append(valid_loss)
        except KeyboardInterrupt:
            tq.close()
            print('Ctrl+C, saving snapshot')
            save(epoch)
            print('done.')
            return