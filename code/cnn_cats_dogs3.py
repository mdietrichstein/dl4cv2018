from dlvc.dataset import Subset
from dlvc.datasets.pets import PetsDataset
from dlvc.test import Accuracy
from dlvc.models import knn, pytorch as cnn
from dlvc import ops, batches

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import matplotlib.pyplot as plt

INPUT_DIM = 3072
NUM_CLASSES = 2


def load_dataset(subset: Subset, augment=False) -> batches.BatchGenerator:
    dataset = PetsDataset('../data/cifar-10-batches-py', subset)

    ops_list = []

    if augment:
        ops_list += [
            ops.hflip(),
            ops.rcrop(32, 8, 'constant')
        ]

    ops_list += [
        # ops.add(-127.5),
        ops.mul(1 / 255),
        ops.type_cast(np.float32),
        ops.normalize(  mean=np.array([0.485, 0.456, 0.406]),
                        std=np.array([0.229, 0.224, 0.225])),
        ops.hwc2chw()
    ]

    op = ops.chain(ops_list)

    return batches.BatchGenerator(dataset, 128, True, op)

class Net(nn.Module):
    def __init__(self, dropout_probability=None):
        super(Net, self).__init__()
        # Deep conv filters produce better results
        # Adding more conv layers does not seem to help
        
        self.dropout = None

        if dropout_probability:
            self.dropout = nn.Dropout(dropout_probability)

        self.pool = nn.MaxPool2d(2, 2)
        self.conv1 = nn.Conv2d(3, 64, 5)
        self.conv2 = nn.Conv2d(64, 128, 5)
        # self.conv3 = nn.Conv2d(128, 256, 5)
        # self.fc1 = nn.Linear(256 * 1 * 1, 128)
        self.fc1 = nn.Linear(128 * 5 * 5, 128)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, 2)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        # if self.dropout:
        #     x = self.dropout(x)
        x = self.pool(F.relu(self.conv2(x)))
        # if self.dropout:
        #     x = self.dropout(x)
        # x = self.pool(F.relu(self.conv3(x)))
        # if self.dropout:
        #     x = self.dropout(x)
        # x = x.view(-1, 256 * 1 * 1)
        x = x.view(-1, 128 * 5 * 5)
        x = F.relu(self.fc1(x))
        if self.dropout:
            x = self.dropout(x)
        x = F.relu(self.fc2(x))
        if self.dropout:
            x = self.dropout(x)
        x = self.fc3(x)
        return x

import torchvision.models as models

class PretrainedResnet18Net(nn.Module):
    def __init__(self):
        super(PretrainedResnet18Net, self).__init__()
        
        self.model = models.resnet18(pretrained=True)

        self.classifier = nn.Sequential(
            nn.Linear(in_features=512, out_features=4096, bias=True),
            nn.ReLU(),
            nn.Dropout(p=0.5),
            nn.Linear(in_features=4096, out_features=4096, bias=True),
            nn.ReLU(),
            nn.Dropout(p=0.5),
            nn.Linear(in_features=4096, out_features=2, bias=True)
        )

    def enable_grad(self, enable):
        for param in self.model.parameters():
            param.requires_grad = enable
        
    def forward(self, x):
        x = self.model.conv1(x)
        x = self.model.bn1(x)
        x = self.model.relu(x)
        x = self.model.maxpool(x)
        x = self.model.layer1(x)
        x = self.model.layer2(x)
        x = self.model.layer3(x)
        x = self.model.layer4(x)
        # x = self.model.avgpool(x)
        # print(x.size())
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x

class PretrainedVGG16BnNet(nn.Module):
    def __init__(self):
        super(PretrainedVGG16BnNet, self).__init__()
        
        model = models.vgg11_bn(pretrained=True)

        self.features = model.features

        classifier_layers = list(model.classifier.children())[1:-1]
        classifier_layers = [nn.Linear(512, 4096, bias=True)] + classifier_layers + [nn.Linear(4096, 2, bias=True)]
        self.classifier = nn.Sequential(*classifier_layers)

    def enable_grad(self, enable):
        for param in self.features.parameters():
            param.requires_grad = enable

    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x


def get_standard_model(dropout_probability=None):
    model = Net(dropout_probability)
    return model

def get_pretrained_model():
    # return PretrainedVGG16BnNet()
    return PretrainedResnet18Net()

    # # model = models.resnet18(pretrained=True)
    # # model = models.vgg11(pretrained=True)
    # model = models.vgg11_bn(pretrained=True)

    # print(model)
    # for param in model.parameters():
    #     param.requires_grad = False

    # # num_features = model.fc.in_features
    # num_features = model.classifier[-1].in_features
    # print(num_features)

    # final_layer = nn.Linear(num_features, 2)
    
    # if dropout_probability:
    #     final_layer = nn.Sequential(
    #         nn.Dropout(dropout_probability),
    #         final_layer)
    
    # # model.fc = final_layer
    # model.classifier[-1] = final_layer
    # return model

if __name__ == "__main__":
    best_model_path = 'best_model.pth'

    training_batch = load_dataset(Subset.TRAINING, augment=True)
    validation_batch = load_dataset(Subset.VALIDATION)

    model = get_pretrained_model()
    model.enable_grad(False)  # fix pre-trained layer weights

    if torch.cuda.is_available():
        model = model.cuda()

    learning_rate = 0.01
    weight_decay = 0.001

    cnn_cl = cnn.CnnClassifier(model, (3, 32, 32), num_classes=2, lr=learning_rate, wd=weight_decay)

    loss_list = []
    measure = Accuracy()
    accuracies = []
    mean_loss_list = []

    best_accuracy = 0
    for epoch in range(1, 101):
        if epoch == 50:
            model.enable_grad(True) # enable fine-tuning of pre-trained layers at epoch 50
        
        predictions = np.zeros((1, 2))
        loss_list = []
        labels = []
        for training_data in training_batch:
            loss = cnn_cl.train(training_data.data, training_data.label)
            loss_list.append(loss)

        mean_loss = np.array(loss_list).mean()
        std_loss = np.array(loss_list).std()
        print('epoch: {}'.format(epoch))
        print('\ttrain loss: {0:0.3f} +- {1:0.3f}'.format(mean_loss, std_loss))
        mean_loss_list.append(mean_loss)

        for validation_data in validation_batch:
            prediction = cnn_cl.predict(validation_data.data)
            predictions = np.vstack((predictions, prediction))
            labels.append(validation_data.label)

        predictions = predictions[1:]
        labels = np.array(labels)
        labels = np.hstack(labels)

        measure.reset()
        measure.update(predictions, labels)
        print('\tval acc: accuracy: {0:0.3f}'.format(measure.accuracy()))

        if measure.accuracy() > best_accuracy:
            best_accuracy = measure.accuracy()
            torch.save(model.state_dict(), best_model_path)
            print('\tNew best accuracy. Saved model to: "{}"'.format(best_model_path))

        accuracies.append(measure.accuracy())
    
    print('Best accuracy: {0:0.3f}'.format(best_accuracy))
    