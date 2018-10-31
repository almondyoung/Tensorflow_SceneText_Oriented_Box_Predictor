# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Faster RCNN feature extractor based on VGG16 network.

See "Inception-v4, Inception-ResNet and the Impact of Residual Connections on
Learning" by Szegedy et al. (https://arxiv.org/abs/1602.07261)
as well as
"Speed/accuracy trade-offs for modern convolutional object detectors" by
Huang et al. (https://arxiv.org/abs/1611.10012)
"""

import tensorflow as tf

from meta_architectures import faster_rcnn_meta_arch
from nets import mobilenet_v1
from nets import inception_resnet_v2
from models import feature_map_generators

slim = tf.contrib.slim


class FasterRCNNMobileNetV1FeatureExtractor(
    faster_rcnn_meta_arch.FasterRCNNFeatureExtractor):
  """Faster R-CNN with Inception Resnet v2 feature extractor implementation."""

  def __init__(self,
               is_training,
               first_stage_features_stride,
               reuse_weights=None,
               weight_decay=0.0):
    """Constructor.

    Args:
      is_training: See base class.
      first_stage_features_stride: See base class.
      reuse_weights: See base class.
      weight_decay: See base class.

    Raises:
      ValueError: If `first_stage_features_stride` is not 8 or 16.
    """
    super(FasterRCNNMobileNetV1FeatureExtractor, self).__init__(
        is_training, first_stage_features_stride, reuse_weights, weight_decay)

  def preprocess(self, resized_inputs):
    """Faster R-CNN with Inception Resnet v2 preprocessing.

    Maps pixel values to the range [-1, 1].

    Args:
      resized_inputs: A [batch, height_in, width_in, channels] float32 tensor
        representing a batch of images with values between 0 and 255.0.

    Returns:
      preprocessed_inputs: A [batch, height_out, width_out, channels] float32
        tensor representing a batch of images.

    """
    return (2.0 / 255.0) * resized_inputs - 1.0

  def _extract_proposal_features(self, preprocessed_inputs, scope):
    """Extracts first stage RPN features.

    Extracts features using the first half of the Inception Resnet v2 network.
    We construct the network in `align_feature_maps=True` mode, which means
    that all VALID paddings in the network are changed to SAME padding so that
    the feature maps are aligned.

    Args:
      preprocessed_inputs: A [batch, height, width, channels] float32 tensor
        representing a batch of images.
      scope: A scope name.

    Returns:
      rpn_feature_map: A tensor with shape [batch, height, width, depth]
    Raises:
      InvalidArgumentError: If the spatial size of `preprocessed_inputs`
        (height or width) is less than 33.
      ValueError: If the created network is missing the required activation.
    """
    preprocessed_inputs.get_shape().assert_has_rank(4)
    shape_assert = tf.Assert(
        tf.logical_and(tf.greater_equal(tf.shape(preprocessed_inputs)[1], 33),
                       tf.greater_equal(tf.shape(preprocessed_inputs)[2], 33)),
        ['image size must at least be 33 in both height and width.'])
    feature_map_layout = {
        'from_layer': ['Conv2d_11_pointwise', 'Conv2d_13_pointwise', '', '',
                       '', ''],
        'layer_depth': [-1, -1, 512, 256, 256, 128],
    }
    depth_multiplier = 1.0
    min_depth = 16
    with tf.control_dependencies([shape_assert]):
      with slim.arg_scope(mobilenet_v1.mobilenet_v1_arg_scope(
        is_training=self._is_training,
        weight_decay=self._weight_decay)):
        # Forces is_training to False to disable batch norm update.
        with slim.arg_scope([slim.batch_norm], is_training=False):
          with tf.variable_scope('MobileNetV1',
                               reuse=self._reuse_weights) as scope:
            _, image_features= mobilenet_v1.mobilenet_v1_base(preprocessed_inputs,
                  scope=scope)
            #feature_maps = feature_map_generators.multi_resolution_feature_maps(
            #  feature_map_layout=feature_map_layout,
            #  depth_multiplier=depth_multiplier,
            #  min_depth=min_depth,
            #  insert_1x1_conv=True,
            #  image_features=image_features)
            #_, end_points = vgg.vgg_16(preprocessed_inputs, scope=scope,
          #    is_training=self._is_training)
            rpn_feature_map = image_features['Conv2d_13_pointwise']
    return rpn_feature_map

  def _extract_box_classifier_features(self, proposal_feature_maps, scope):
    """Extracts second stage box classifier features.

    This function reconstructs the "second half" of the Inception ResNet v2
    network after the part defined in `_extract_proposal_features`.

    Args:
      proposal_feature_maps: A 4-D float tensor with shape
        [batch_size * self.max_num_proposals, crop_height, crop_width, depth]
        representing the feature map cropped to each proposal.
      scope: A scope name.

    Returns:
      proposal_classifier_features: A 4-D float tensor with shape
        [batch_size * self.max_num_proposals, height, width, depth]
        representing box classifier features for each proposal.
    """

    with tf.variable_scope('VGG16', reuse=self._reuse_weights):
      with slim.arg_scope(mobilenet_v1.mobilenet_v1_arg_scope(
        is_training=self._is_training,
        weight_decay=self._weight_decay)):
        # Forces is_training to False to disable batch norm update.
        with slim.arg_scope([slim.batch_norm], is_training=False):
          with slim.arg_scope([slim.conv2d, slim.max_pool2d, slim.avg_pool2d],
                              stride=1, padding='SAME'):
            with tf.variable_scope('Mixed_7a'):
              with tf.variable_scope('Branch_0'):
                tower_conv = slim.conv2d(proposal_feature_maps,
                                         256, 1, scope='Conv2d_0a_1x1')
                tower_conv_1 = slim.conv2d(
                    tower_conv, 384, 3, stride=2,
                    padding='VALID', scope='Conv2d_1a_3x3')
              with tf.variable_scope('Branch_1'):
                tower_conv1 = slim.conv2d(
                    proposal_feature_maps, 256, 1, scope='Conv2d_0a_1x1')
                tower_conv1_1 = slim.conv2d(
                    tower_conv1, 288, 3, stride=2,
                    padding='VALID', scope='Conv2d_1a_3x3')
              with tf.variable_scope('Branch_2'):
                tower_conv2 = slim.conv2d(
                    proposal_feature_maps, 256, 1, scope='Conv2d_0a_1x1')
                tower_conv2_1 = slim.conv2d(tower_conv2, 288, 3,
                                            scope='Conv2d_0b_3x3')
                tower_conv2_2 = slim.conv2d(
                    tower_conv2_1, 320, 3, stride=2,
                    padding='VALID', scope='Conv2d_1a_3x3')
              with tf.variable_scope('Branch_3'):
                tower_pool = slim.max_pool2d(
                    proposal_feature_maps, 3, stride=2, padding='VALID',
                    scope='MaxPool_1a_3x3')
              net = tf.concat(
                  [tower_conv_1, tower_conv1_1, tower_conv2_2, tower_pool], 3)
            net = slim.repeat(net, 9, inception_resnet_v2.block8, scale=0.20)
            net = inception_resnet_v2.block8(net, activation_fn=None)
            proposal_classifier_features = slim.conv2d(
                net, 1536, 1, scope='Conv2d_7b_1x1')
        return proposal_classifier_features

  def restore_from_classification_checkpoint_fn(
      self,
      first_stage_feature_extractor_scope,
      second_stage_feature_extractor_scope):
    """Returns a map of variables to load from a foreign checkpoint.

    Note that this overrides the default implementation in
    faster_rcnn_meta_arch.FasterRCNNFeatureExtractor which does not work for
    InceptionResnetV2 checkpoints.

    TODO: revisit whether it's possible to force the
    `Repeat` namescope as created in `_extract_box_classifier_features` to
    start counting at 2 (e.g. `Repeat_2`) so that the default restore_fn can
    be used.

    Args:
      first_stage_feature_extractor_scope: A scope name for the first stage
        feature extractor.
      second_stage_feature_extractor_scope: A scope name for the second stage
        feature extractor.

    Returns:
      A dict mapping variable names (to load from a checkpoint) to variables in
      the model graph.
    """

    variables_to_restore = {}
    for variable in tf.global_variables():
      if variable.op.name.startswith(
          first_stage_feature_extractor_scope):
        var_name = variable.op.name.replace(
            first_stage_feature_extractor_scope + '/', '')
        variables_to_restore[var_name] = variable
      if variable.op.name.startswith(
          second_stage_feature_extractor_scope):
        var_name = variable.op.name.replace(
            second_stage_feature_extractor_scope
            + '/InceptionResnetV2/Repeat', 'InceptionResnetV2/Repeat_2')
        var_name = var_name.replace(
            second_stage_feature_extractor_scope + '/', '')
        variables_to_restore[var_name] = variable
    return variables_to_restore