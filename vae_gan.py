"""
    VAE-GAN: combination of Encoder, Generator and Discriminator network

    based on: https://arxiv.org/pdf/1512.09300.pdf
"""

import os
from tensorflow.contrib import layers
import tensorflow as tf
from time import strftime

from visualizer import ReconstructionVisualizer


class VAE_GAN:

    def __init__(self, hidden_size, e_learning_rate=5e-4, g_learning_rate=5e-4, d_learning_rate=5e-4, image_size=64):
        self.experiment_name = "VAE-GAN_" + strftime("%Y%m%d_%H%M%S")

        self.image_size = image_size

        self.x = tf.placeholder(tf.float32, [None, image_size * image_size])

        self.batch_size = tf.shape(self.x)[0]

        self.z_p = tf.random_normal((self.batch_size, hidden_size), 0, 1)  # normal dist for GAN
        self.eps = tf.random_normal((self.batch_size, hidden_size), 0, 1)  # normal dist for VAE

        self.global_step = tf.Variable(0, name='global_step', trainable=False)

        with tf.variable_scope("encode"):
            self.z_x_mean, self.z_x_log_sigma_sq = self._encoder(self.x, hidden_size)  # get z from the input

        with tf.variable_scope("generate"):
            self.z_x = tf.add(self.z_x_mean, tf.multiply(tf.sqrt(tf.exp(self.z_x_log_sigma_sq)), self.eps))  # grab our actual z
            self.x_tilde = self._generator(self.z_x)

        with tf.variable_scope("discriminate"):
            _, l_x_tilde = self._discriminator(self.x_tilde)

        with tf.variable_scope("generate", reuse=True):
            self.x_p = self._generator(self.z_p)

        with tf.variable_scope("discriminate", reuse=True):
            self.d_x, l_x = self._discriminator(self.x)  # positive examples

        with tf.variable_scope("discriminate", reuse=True):
            self.d_x_p, _ = self._discriminator(self.x_p)

        with tf.variable_scope("loss"):
            SSE_loss = tf.reduce_mean(tf.square(self.x - self.x_tilde))  # This is what a normal VAE uses

            # We clip gradients of KL divergence to prevent NANs
            self.KL_loss = tf.reduce_sum(-0.5 * tf.reduce_sum(1 + tf.clip_by_value(self.z_x_log_sigma_sq, -10.0, 10.0)
                                           - tf.square(tf.clip_by_value(self.z_x_mean, -10.0, 10.0))
                                           - tf.exp(tf.clip_by_value(self.z_x_log_sigma_sq, -10.0, 10.0)), 1))/(image_size * image_size)

            # Discriminator Loss
            self.D_loss = tf.reduce_mean(-1. * (tf.log(tf.clip_by_value(self.d_x, 1e-5, 1.0)) + tf.log(tf.clip_by_value(1.0 - self.d_x_p, 1e-5, 1.0))))

            # Generator Loss
            self.G_loss = tf.reduce_mean(-1. * (tf.log(tf.clip_by_value(self.d_x_p, 1e-5, 1.0))))  # + tf.log(tf.clip_by_value(1.0 - d_x,1e-5,1.0))))

            # Lth Layer Loss - the 'learned similarity measure'
            self.LL_loss = tf.reduce_sum(tf.square(l_x - l_x_tilde))/(image_size * image_size)

            # summary
            with tf.name_scope("loss_summary"):
                tf.summary.histogram("LL_loss", self.LL_loss)

        with tf.variable_scope("optimizer"):

            # specify loss to parameters
            params = tf.trainable_variables()
            E_params = [i for i in params if 'enc' in i.name]
            G_params = [i for i in params if 'gen' in i.name]
            D_params = [i for i in params if 'dis' in i.name]

            #KL_param, LL_param, G_param = 1., 1., 1.

            # Calculate the losses specific to encoder, generator, decoder
            L_e = tf.clip_by_value(self.KL_loss + self.LL_loss, -100, 100)
            L_g = tf.clip_by_value(self.LL_loss + self.G_loss, -100, 100)
            L_d = tf.clip_by_value(self.D_loss, -100, 100)

            optimizer_E = tf.train.AdamOptimizer(e_learning_rate, epsilon=1.0)
            grads = optimizer_E.compute_gradients(L_e, var_list=E_params)
            self.train_E = optimizer_E.apply_gradients(grads, global_step=self.global_step)

            optimizer_G = tf.train.AdamOptimizer(g_learning_rate, epsilon=1.0)
            grads = optimizer_G.compute_gradients(L_g, var_list=G_params)
            self.train_G = optimizer_G.apply_gradients(grads, global_step=self.global_step)

            optimizer_D = tf.train.AdamOptimizer(d_learning_rate, epsilon=1.0)
            grads = optimizer_D.compute_gradients(L_d, var_list=D_params)
            self.train_D = optimizer_D.apply_gradients(grads, global_step=self.global_step)

        # check tensors
        self._check_tensors()

        # init session
        self.sess = tf.Session()

        self.merged_summary_op = tf.summary.merge_all()
        self.summary_writer = tf.summary.FileWriter(os.path.join("tensorboard", self.experiment_name), self.sess.graph)

        self.sess.run(tf.global_variables_initializer())

    def _encoder(self, input_tensor, output_size, image_size=64):
        net = tf.reshape(input_tensor, [self.batch_size, image_size, image_size, 1])
        net1 = layers.conv2d(net, 32, 5, stride=2)
        net2 = layers.conv2d(net1, 64, 5, stride=2)
        net3 = layers.conv2d(net2, 128, 5, stride=2, padding='VALID')
        net = tf.nn.dropout(net3, keep_prob=0.9)
        net = layers.flatten(net)
        net = layers.fully_connected(net, 2 * output_size)
        z_mean = net[:, :output_size]
        z_log_sigma_q = net[:, output_size:]

        # summary
        with tf.name_scope("encoder_summary"):
            tf.summary.image("net1",  tf.expand_dims(tf.reduce_mean(net1, axis=-1), axis=-1))

        return z_mean, z_log_sigma_q

    def _generator(self, input_tensor):
        net = tf.expand_dims(input_tensor, 1)
        net = tf.expand_dims(net, 1)
        net = layers.conv2d_transpose(net, 128, 8, padding='VALID')
        net = layers.conv2d_transpose(net, 64, 5, stride=2)
        net = layers.conv2d_transpose(net, 32, 5, stride=2)
        net = layers.conv2d_transpose(net, 1, 5, stride=2)
        net = tf.nn.sigmoid(net)
        net = layers.flatten(net)
        return net

    def _discriminator(self, input_tensor, image_size=64):
        net = tf.reshape(input_tensor, [self.batch_size, image_size, image_size, 1])
        net = layers.conv2d(net, 32, 5, stride=2)
        net = layers.conv2d(net, 64, 5, stride=2)
        net = layers.conv2d(net, 128, 5, stride=2, padding='VALID')
        net = tf.nn.dropout(net, keep_prob=0.9)
        net = layers.flatten(net)
        lth_layer = layers.fully_connected(net, 1024, activation_fn=tf.nn.elu)
        D = layers.fully_connected(lth_layer, 1, activation_fn=tf.nn.sigmoid)
        return D, lth_layer

    def _check_tensors(self):
        if tf.trainable_variables():
            for v in tf.trainable_variables():
                print("%s : %s" % (v.name, v.get_shape()))

    def update_params(self, x):
        _, _, _, D_err, G_err, KL_err, LL_err, d_fake, d_real = self.sess.run([
            self.train_E, self.train_G, self.train_D,
            self.D_loss, self.G_loss, self.KL_loss, self.LL_loss,
            self.d_x_p, self.d_x], feed_dict={self.x: x})

        # summary update
        summary = self.sess.run(self.merged_summary_op, feed_dict={self.x: x})
        self.summary_writer.add_summary(summary, global_step=tf.train.global_step(self.sess, self.global_step))

        return D_err, G_err, KL_err, LL_err, d_fake, d_real

    def generate_and_save_images(self, num_samples, directory, epoch, x):
        # create experiment folder
        experiment_dir = os.path.join(directory, self.experiment_name)

        if not os.path.exists(experiment_dir):
            os.makedirs(experiment_dir)
            print('created directory:', experiment_dir)

        random_x, recon_z, all_d = self.sess.run((self.x_p, self.z_x_mean, self.d_x_p), feed_dict={self.x: x})
        recon_x = self.sess.run(self.x_tilde, feed_dict={self.z_x: recon_z})

        visualizer = ReconstructionVisualizer(experiment_dir, image_size=self.image_size)
        visualizer.save_generated_samples(random_x, recon_x, x, epoch)

    def print_loss(self, x, epoch):
        D_err, G_err, KL_err, LL_err = self.sess.run([self.D_loss, self.G_loss, self.KL_loss, self.LL_loss], feed_dict={self.x: x})
        print("epoch: %3d," % epoch, "D loss: %.6f," % D_err, "G loss: %.6f," % G_err, "KL loss: %.6f," % KL_err, "LL loss: %.6f," % LL_err)
