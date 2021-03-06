"""This tutorial introduces restricted boltzmann machines (RBM) using Theano.

Boltzmann Machines (BMs) are a particular form of energy-based model which
contain hidden variables. Restricted Boltzmann Machines further restrict BMs
to those without visible-visible and hidden-hidden connections.
"""

from __future__ import print_function

import timeit

try:
    import PIL.Image as Image
except ImportError:
    import Image

import numpy
import h5py
import scipy.io as sio

import theano
import theano.tensor as T
import os

from theano.tensor.shared_randomstreams import RandomStreams

from utils import tile_raster_images
from logistic_sgd import load_data


# start-snippet-1
class DRBM(object):
    """Restricted Boltzmann Machine (DRBM)  """
    def __init__(
        self,
        input=None,
        input_label=None,
        n_visible=784,
        n_hidden=500,
        n_label=2,
        U=None,
        W=None,
        labbias=None,
        hbias=None,
        vbias=None,
        numpy_rng=None,
        theano_rng=None,
        alpha=0.01
    ):
        """
        DRBM constructor. Defines the parameters of the model along with
        basic operations for inferring hidden from visible (and vice-versa),
        as well as for performing CD updates.

        :param input: None for standalone RBMs or symbolic variable if RBM is
        part of a larger graph.

        :param n_visible: number of visible units

        :param n_hidden: number of hidden units

        :param W: None for standalone RBMs or symbolic variable pointing to a
        shared weight matrix in case RBM is part of a DBN network; in a DBN,
        the weights are shared between RBMs and layers of a MLP

        :param hbias: None for standalone RBMs or symbolic variable pointing
        to a shared hidden units bias vector in case RBM is part of a
        different network

        :param vbias: None for standalone RBMs or a symbolic variable
        pointing to a shared visible units bias
        """

        self.n_visible = n_visible
        self.n_hidden = n_hidden
        self.n_label = n_label
        self.alpha = alpha

        if numpy_rng is None:
            # create a number generator
            numpy_rng = numpy.random.RandomState(1234)

        if theano_rng is None:
            theano_rng = RandomStreams(numpy_rng.randint(2 ** 30))

        if U is None:
            initial_U = numpy.asarray(
                numpy_rng.uniform(
                    low=-4 * numpy.sqrt(6. / (n_visible + n_label)),
                    high=4 * numpy.sqrt(6. / (n_visible + n_label)),
                    size=(n_label, n_hidden)
                ),
                dtype=theano.config.floatX
            )
            U = theano.shared(initial_U, name='U', borrow=True)

        if W is None:
            # W is initialized with `initial_W` which is uniformely
            # sampled from -4*sqrt(6./(n_visible+n_hidden)) and
            # 4*sqrt(6./(n_hidden+n_visible)) the output of uniform if
            # converted using asarray to dtype theano.config.floatX so
            # that the code is runable on GPU
            initial_W = numpy.asarray(
                numpy_rng.uniform(
                    low=-4 * numpy.sqrt(6. / (n_hidden + n_visible)),
                    high=4 * numpy.sqrt(6. / (n_hidden + n_visible)),
                    size=(n_visible, n_hidden)
                ),
                dtype=theano.config.floatX
            )
            # theano shared variables for weights and biases
            W = theano.shared(value=initial_W, name='W', borrow=True)

        if hbias is None:
            # create shared variable for hidden units bias
            hbias = theano.shared(
                value=numpy.zeros(
                    n_hidden,
                    dtype=theano.config.floatX
                ),
                name='hbias',
                borrow=True
            )

        if vbias is None:
            # create shared variable for visible units bias
            vbias = theano.shared(
                value=numpy.zeros(
                    n_visible,
                    dtype=theano.config.floatX
                ),
                name='vbias',
                borrow=True
            )

        if labbias is None:
            labbias = theano.shared(
                value=numpy.zeros(
                    n_label,
                    dtype=theano.config.floatX
                ),
                name='labbias',
                borrow=True
            )

        # initialize input layer for standalone RBM or layer0 of DBN
        self.input = input
        if not input:
            self.input = T.matrix('input')

        self.input_label = input_label
        self.numpy_rng = numpy_rng

        self.U = U
        self.W = W
        self.hbias = hbias
        self.vbias = vbias
        self.labbias = labbias
        self.theano_rng = theano_rng
        # **** WARNING: It is not a good idea to put things in this list
        # other than shared variables created in this function.
        self.params = [self.W, self.hbias, self.vbias, self.U, self.labbias]
        # end-snippet-1

    # Updated by Febrian 18/05/2016
    # Free energy for Discriminative RBM
    # F(y,x) = d_y + sum_j softplus(c_j + u_jy + sum_i w_ji * x_i)
    # where: y --> class y, i --> data i, and j --> unit j in hidden layer
    def free_energy(self, v_sample, lab_sample):
        ''' Function to compute the free energy '''
        # wx_b = T.dot(v_sample, self.W) + self.hbias + T.dot(lab_sample, self.U)
        # vbias_term = T.dot(v_sample, self.vbias)
        # hidden_term = T.sum(T.log(1 + T.exp(wx_b)), axis=1)
        # label_term = T.dot(lab_sample, self.labbias)

        wx_b = T.dot(v_sample, self.W) + self.hbias + T.dot(lab_sample, self.U)
        hidden_term = T.sum(T.nnet.softplus(wx_b), axis=1)
        label_term = T.dot(lab_sample, self.labbias.T)

        return -hidden_term -label_term

    # Created by Febrian (24/05/2016)
    # Precompute c_j + U_jy + sum_i (W_ji * x_i)
    def precompute_thingy(self, v_sample, batch_size):
        precomputed = T.dot(v_sample, self.W) + self.hbias
        precomputed_labs = numpy.zeros([self.n_label, batch_size, self.n_hidden],
            dtype=theano.config.floatX)
        for ii in range(0, self.n_label):
            precomputed_labs[ii] = precomputed #+ self.U[ii]
        return theano.shared(precomputed_labs)

    def precompute_a(self, v_sample):
        precomputed = T.dot(v_sample, self.W) + self.hbias
        return precomputed

    # Created by Febrian (24/05/2016)
    def sample_y_given_x(self, v_sample, batch_size):
        pc = theano.shared(self.precompute_thingy(v_sample, batch_size))
        neg_free_energy = -T.sum(T.nnet.softplus(pc), axis=1).T - self.labbias
        neg_free_energy_exp = T.exp(neg_free_energy - T.mean(neg_free_energy))
        sum_all = T.sum(neg_free_energy_exp, axis=1)
        for ii in range(0, batch_size):
            neg_free_energy_exp[ii] = neg_free_energy_exp[ii] / sum_all[ii]
        return neg_free_energy_exp

    # Updated by Febrian 18/05/2016
    def propup(self, vis, lab):
        '''This function propagates the visible units activation upwards to
        the hidden units

        Note that we return also the pre-sigmoid activation of the
        layer. As it will turn out later, due to how Theano deals with
        optimizations, this symbolic variable will be needed to write
        down a more stable computational graph (see details in the
        reconstruction cost function)

        '''
        pre_sigmoid_activation = T.dot(vis, self.W) + self.hbias + T.dot(lab, self.U)
        return [pre_sigmoid_activation, T.nnet.sigmoid(pre_sigmoid_activation)]

    # Updated by Febrian 18/05/2016
    def sample_h_given_v(self, v0_sample, l0_sample):
        ''' This function infers state of hidden units given visible units '''
        # compute the activation of the hidden units given a sample of
        # the visibles
        pre_sigmoid_h1, h1_mean = self.propup(v0_sample, l0_sample)
        # get a sample of the hiddens given their activation
        # Note that theano_rng.binomial returns a symbolic sample of dtype
        # int64 by default. If we want to keep our computations in floatX
        # for the GPU we need to specify to return the dtype floatX
        h1_sample = self.theano_rng.binomial(size=h1_mean.shape,
                                             n=1, p=h1_mean,
                                             dtype=theano.config.floatX)
        return [pre_sigmoid_h1, h1_mean, h1_sample]

    def propdown(self, hid):
        '''This function propagates the hidden units activation downwards to
        the visible units

        Note that we return also the pre_sigmoid_activation of the
        layer. As it will turn out later, due to how Theano deals with
        optimizations, this symbolic variable will be needed to write
        down a more stable computational graph (see details in the
        reconstruction cost function)

        '''
        pre_sigmoid_activation = T.dot(hid, self.W.T) + self.vbias
        return [pre_sigmoid_activation, T.nnet.sigmoid(pre_sigmoid_activation)]

    def sample_v_given_h(self, h0_sample):
        ''' This function infers state of visible units given hidden units '''
        # compute the activation of the visible given the hidden sample
        pre_sigmoid_v1, v1_mean = self.propdown(h0_sample)
        # get a sample of the visible given their activation
        # Note that theano_rng.binomial returns a symbolic sample of dtype
        # int64 by default. If we want to keep our computations in floatX
        # for the GPU we need to specify to return the dtype floatX
        v1_sample = self.theano_rng.binomial(size=v1_mean.shape,
                                             n=1, p=v1_mean,
                                             dtype=theano.config.floatX)
        return [pre_sigmoid_v1, v1_mean, v1_sample]

    # Made by Febrian 18/05/2016
    # This function is used to compute y1 <- p(y|h0)
    def propdown_label(self, hid):
        pre_norm_activation = T.exp(T.dot(hid, self.U.T) + self.labbias)
        norm_factor = T.sum(pre_norm_activation,1)
        norm = norm_factor.reshape(norm_factor.size,1)
        probs = pre_norm_activation / norm
        return [pre_norm_activation, probs]

    # # Made by Febrian 20/05/2016
    # # This function is used to create negative label based on probabilities
    # def sampling_label(self, probs):
    #     batch_size = probs.get_value().shape[0]
    #     xx = numpy.cumsum(probs,1)
    #     xx1 = rng.rand(batch_size,1)
    #     neg_labs = numpy.zeros([batch_size, self.n_label])
    #     for jj in range(0, batch_size):
    #         index = numpy.min(numpy.where(numpy.less_equal(xx1[jj],xx[jj])))
    #         neg_labs[jj,index] = 1
    #     return neg_labs

    # Made by Febrian 18/05/2016
    # This function is used to compute probability of sampled labels
    def sample_lab_given_h(self, h0_sample):
        pre_norm_lab1, lab1_mean = self.propdown_label(h0_sample)
        # lab1_sample = sampling_label(lab1_mean)

        probs = lab1_mean
        batch_size = 20
        xx = theano.tensor.extra_ops.cumsum(probs,1)
        xx1 = self.numpy_rng.rand(batch_size,1)
        neg_labs = numpy.zeros([batch_size, self.n_label], dtype=theano.config.floatX)
        for jj in range(0, batch_size):
            index = numpy.min(numpy.where(numpy.less_equal(xx1[jj],xx[jj])))
            neg_labs[jj,index] = 1

        neg_labs_theano = theano.shared(neg_labs)

        return [pre_norm_lab1, lab1_mean, neg_labs_theano]

    # Updated by Febrian 18/05/2016
    # Adding pre_sigmoid_l1, l1_mean, l1_sample = self.sample_lab_given_h(h1_sample)
    def gibbs_hvh(self, h0_sample, l0_sample):
        ''' This function implements one step of Gibbs sampling,
            starting from the hidden state'''
        pre_sigmoid_v1, v1_mean, v1_sample = self.sample_v_given_h(h0_sample)
        pre_sigmoid_h1, h1_mean, h1_sample = self.sample_h_given_v(v1_sample, l0_sample)
        pre_sigmoid_l1, l1_mean, l1_sample = self.sample_lab_given_h(h1_sample)
        return [pre_sigmoid_v1, v1_mean, v1_sample,
                pre_sigmoid_h1, h1_mean, h1_sample,
                pre_sigmoid_l1, l1_mean, l1_sample]

    # Updated by Febrian 18/05/2016
    # Adding pre_sigmoid_l1, l1_mean, l1_sample = self.sample_lab_given_h(h1_sample)
    def gibbs_vhv(self, v0_sample):
        ''' This function implements one step of Gibbs sampling,
            starting from the visible state'''
        pre_sigmoid_h1, h1_mean, h1_sample = self.sample_h_given_v(v0_sample)
        pre_sigmoid_v1, v1_mean, v1_sample = self.sample_v_given_h(h1_sample)
        pre_sigmoid_l1, l1_mean, l1_sample = self.sample_lab_given_h(h1_sample)
        return [pre_sigmoid_h1, h1_mean, h1_sample,
                pre_sigmoid_v1, v1_mean, v1_sample,
                pre_sigmoid_l1, l1_mean, l1_sample]

    # start-snippet-2
    def get_cost_updates(self, lr=0.1, persistent=None, neglab=None, k=1, batch_size=20):
        """This functions implements one step of CD-k or PCD-k

        :param lr: learning rate used to train the RBM

        :param persistent: None for CD. For PCD, shared variable
            containing old state of Gibbs chain. This must be a shared
            variable of size (batch size, number of hidden units).

        :param k: number of Gibbs steps to do in CD-k/PCD-k

        Returns a proxy for the cost and the updates dictionary. The
        dictionary contains the update rules for weights and biases but
        also an update of the shared variable used to store the persistent
        chain, if one is used.

        """

        # compute positive phase
        pre_sigmoid_ph, ph_mean, ph_sample = self.sample_h_given_v(self.input, self.input_label)

        # decide how to initialize persistent chain:
        # for CD, we use the newly generate hidden sample
        # for PCD, we initialize from the old state of the chain
        if persistent is None:
            chain_start = ph_sample
        else:
            chain_start = persistent

        if neglab is None:
            chain_lab_start = self.input_label
        else:
            chain_lab_start = neglab

        # end-snippet-2
        # perform actual negative phase
        # in order to implement CD-k/PCD-k we need to scan over the
        # function that implements one gibbs step k times.
        # Read Theano tutorial on scan for more information :
        # http://deeplearning.net/software/theano/library/scan.html
        # the scan will return the entire Gibbs chain
        (
            [
                pre_sigmoid_nvs,
                nv_means,
                nv_samples,
                pre_sigmoid_nhs,
                nh_means,
                nh_samples,
                pre_sigmoid_nls,
                lh_means,
                lh_samples,
            ],
            updates
        ) = theano.scan(
            self.gibbs_hvh,
            # the None are place holders, saying that
            # chain_start is the initial state corresponding to the
            # 6th output
            outputs_info=[None, None, None, None, None, chain_start, None, None, chain_lab_start],
            n_steps=k,
            name="gibbs_hvh"
        )
        # start-snippet-3
        # determine gradients on RBM parameters
        # note that we only need the sample at the end of the chain
        chain_end = nv_samples[-1]
        chain_end_labels = lh_samples[-1]

        # cost for generative rbm
        cost_generative = T.mean(self.free_energy(
            self.input, self.input_label)) - T.mean(self.free_energy(
            chain_end, chain_end_labels))

        # cost for discriminative
        y_sampled = self.sample_y_given_x(chain_end, batch_size)
        cost_discriminative = T.mean(T.sum(self.input_label - y_sampled))

        cost = cost_discriminative + (self.alpha * cost_generative)

        # We must not compute the gradient through the gibbs sampling
        gparams = T.grad(cost, self.params, consider_constant=[chain_end])
        # end-snippet-3 start-snippet-4
        # constructs the update dictionary
        for gparam, param in zip(gparams, self.params):
            # make sure that the learning rate is of the right dtype
            updates[param] = param - gparam * T.cast(
                lr,
                dtype=theano.config.floatX
            )

        if persistent:
            # Note that this works only if persistent is a shared variable
            updates[persistent] = nh_samples[-1]
            # pseudo-likelihood is a better proxy for PCD
            monitoring_cost = self.get_pseudo_likelihood_cost(updates)
        else:
            # reconstruction cross-entropy is a better proxy for CD
            monitoring_cost = self.get_reconstruction_cost(updates,
                                                           pre_sigmoid_nvs[-1])

        return monitoring_cost, updates
        # end-snippet-4

    def get_pseudo_likelihood_cost(self, updates):
        """Stochastic approximation to the pseudo-likelihood"""

        # index of bit i in expression p(x_i | x_{\i})
        bit_i_idx = theano.shared(value=0, name='bit_i_idx')

        # binarize the input image by rounding to nearest integer
        xi = T.round(self.input)

        # calculate free energy for the given bit configuration
        fe_xi = self.free_energy(xi)

        # flip bit x_i of matrix xi and preserve all other bits x_{\i}
        # Equivalent to xi[:,bit_i_idx] = 1-xi[:, bit_i_idx], but assigns
        # the result to xi_flip, instead of working in place on xi.
        xi_flip = T.set_subtensor(xi[:, bit_i_idx], 1 - xi[:, bit_i_idx])

        # calculate free energy with bit flipped
        fe_xi_flip = self.free_energy(xi_flip)

        # equivalent to e^(-FE(x_i)) / (e^(-FE(x_i)) + e^(-FE(x_{\i})))
        cost = T.mean(self.n_visible * T.log(T.nnet.sigmoid(fe_xi_flip -
                                                            fe_xi)))

        # increment bit_i_idx % number as part of updates
        updates[bit_i_idx] = (bit_i_idx + 1) % self.n_visible

        return cost

    def get_reconstruction_cost(self, updates, pre_sigmoid_nv):
        """Approximation to the reconstruction error

        Note that this function requires the pre-sigmoid activation as
        input.  To understand why this is so you need to understand a
        bit about how Theano works. Whenever you compile a Theano
        function, the computational graph that you pass as input gets
        optimized for speed and stability.  This is done by changing
        several parts of the subgraphs with others.  One such
        optimization expresses terms of the form log(sigmoid(x)) in
        terms of softplus.  We need this optimization for the
        cross-entropy since sigmoid of numbers larger than 30. (or
        even less then that) turn to 1. and numbers smaller than
        -30. turn to 0 which in terms will force theano to compute
        log(0) and therefore we will get either -inf or NaN as
        cost. If the value is expressed in terms of softplus we do not
        get this undesirable behaviour. This optimization usually
        works fine, but here we have a special case. The sigmoid is
        applied inside the scan op, while the log is
        outside. Therefore Theano will only see log(scan(..)) instead
        of log(sigmoid(..)) and will not apply the wanted
        optimization. We can not go and replace the sigmoid in scan
        with something else also, because this only needs to be done
        on the last step. Therefore the easiest and more efficient way
        is to get also the pre-sigmoid activation as an output of
        scan, and apply both the log and sigmoid outside scan such
        that Theano can catch and optimize the expression.

        """

        cross_entropy = T.mean(
            T.sum(
                self.input * T.log(T.nnet.sigmoid(pre_sigmoid_nv)) +
                (1 - self.input) * T.log(1 - T.nnet.sigmoid(pre_sigmoid_nv)),
                axis=1
            )
        )

        return cross_entropy


def test_drbm(learning_rate=0.1, training_epochs=15, n_label=2,
             dataset='mnist.pkl.gz', batch_size=20,
             n_chains=20, n_samples=10, output_folder='rbm_plots',
             n_hidden=500):
    """
    Demonstrate how to train and afterwards sample from it using Theano.

    This is demonstrated on MNIST.

    :param learning_rate: learning rate used for training the RBM

    :param training_epochs: number of epochs used for training

    :param dataset: path the the pickled dataset

    :param batch_size: size of a batch used to train the RBM

    :param n_chains: number of parallel Gibbs chains to be used for sampling

    :param n_samples: number of samples to plot for each chain

    """
    datasets = load_data(dataset)
    f = h5py.File('features_dl_all_rand_flair_7.mat')

    train_set_x, train_set_y = datasets[0]
    test_set_x, test_set_y = datasets[2]

    # compute number of minibatches for training, validation and testing
    n_train_batches = train_set_x.get_value(borrow=True).shape[0] // batch_size

    # allocate symbolic variables for the data
    index = T.lscalar()    # index to a [mini]batch
    x = T.matrix('x')  # the data is presented as rasterized images
    y = T.matrix('y')

    rng = numpy.random.RandomState(123)
    theano_rng = RandomStreams(rng.randint(2 ** 30))

    # initialize storage for the persistent chain (state = hidden
    # layer of chain)
    persistent_chain = theano.shared(numpy.zeros((batch_size, n_hidden),
                                                 dtype=theano.config.floatX),
                                     borrow=True)

    neglab_chain = theano.shared(numpy.zeros((batch_size, n_label),
                                                 dtype=theano.config.floatX),
                                     borrow=True)

    # construct the RBM class
    rbm = DRBM(input=x, n_visible=28 * 28, input_label=y,
              n_hidden=n_hidden, numpy_rng=rng, theano_rng=theano_rng)

    # get the cost and the gradient corresponding to one step of CD-15
    cost, updates = rbm.get_cost_updates(lr=learning_rate, neglab=neglab_chain,
                                         persistent=persistent_chain, k=15,
                                         batch_size=batch_size)

    #################################
    #     Training the RBM          #
    #################################
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)
    os.chdir(output_folder)

    # start-snippet-5
    # it is ok for a theano function to have no output
    # the purpose of train_rbm is solely to update the RBM parameters
    train_rbm = theano.function(
        [index],
        cost,
        updates=updates,
        givens={
            x: train_set_x[index * batch_size: (index + 1) * batch_size],
            y: train_set_y[index * batch_size: (index + 1) * batch_size]
        },
        name='train_rbm'
    )

    plotting_time = 0.
    start_time = timeit.default_timer()

    # go through training epochs
    for epoch in range(training_epochs):

        # go through the training set
        mean_cost = []
        for batch_index in range(n_train_batches):
            mean_cost += [train_rbm(batch_index)]

        print('Training epoch %d, cost is ' % epoch, numpy.mean(mean_cost))

        # # Plot filters after each training epoch
        # plotting_start = timeit.default_timer()
        # # Construct image from the weight matrix
        # image = Image.fromarray(
        #     tile_raster_images(
        #         X=rbm.W.get_value(borrow=True).T,
        #         img_shape=(28, 28),
        #         tile_shape=(10, 10),
        #         tile_spacing=(1, 1)
        #     )
        # )
        # image.save('filters_at_epoch_%i.png' % epoch)
        # plotting_stop = timeit.default_timer()
        # plotting_time += (plotting_stop - plotting_start)

    end_time = timeit.default_timer()

    pretraining_time = (end_time - start_time) - plotting_time

    print ('Training took %f minutes' % (pretraining_time / 60.))
    # end-snippet-5 start-snippet-6
    #################################
    #     Sampling from the RBM     #
    #################################
    # find out the number of test samples
    number_of_test_samples = test_set_x.get_value(borrow=True).shape[0]

    # pick random test examples, with which to initialize the persistent chain
    test_idx = rng.randint(number_of_test_samples - n_chains)
    persistent_vis_chain = theano.shared(
        numpy.asarray(
            test_set_x.get_value(borrow=True)[test_idx:test_idx + n_chains],
            dtype=theano.config.floatX
        )
    )
    # end-snippet-6 start-snippet-7
    plot_every = 1000
    # define one step of Gibbs sampling (mf = mean-field) define a
    # function that does `plot_every` steps before returning the
    # sample for plotting
    (
        [
            presig_hids,
            hid_mfs,
            hid_samples,
            presig_vis,
            vis_mfs,
            vis_samples
        ],
        updates
    ) = theano.scan(
        rbm.gibbs_vhv,
        outputs_info=[None, None, None, None, None, persistent_vis_chain],
        n_steps=plot_every,
        name="gibbs_vhv"
    )

    # add to updates the shared variable that takes care of our persistent
    # chain :.
    updates.update({persistent_vis_chain: vis_samples[-1]})
    # construct the function that implements our persistent chain.
    # we generate the "mean field" activations for plotting and the actual
    # samples for reinitializing the state of our persistent chain
    sample_fn = theano.function(
        [],
        [
            vis_mfs[-1],
            vis_samples[-1]
        ],
        updates=updates,
        name='sample_fn'
    )

    # create a space to store the image for plotting ( we need to leave
    # room for the tile_spacing as well)
    image_data = numpy.zeros(
        (29 * n_samples + 1, 29 * n_chains - 1),
        dtype='uint8'
    )
    for idx in range(n_samples):
        # generate `plot_every` intermediate samples that we discard,
        # because successive samples in the chain are too correlated
        vis_mf, vis_sample = sample_fn()
        print(' ... plotting sample %d' % idx)
        image_data[29 * idx:29 * idx + 28, :] = tile_raster_images(
            X=vis_mf,
            img_shape=(28, 28),
            tile_shape=(1, n_chains),
            tile_spacing=(1, 1)
        )

    # construct image
    image = Image.fromarray(image_data)
    image.save('samples.png')
    # end-snippet-7
    os.chdir('../')

if __name__ == '__main__':
    test_drbm()
