#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import division
__author__ = 'Jonas Wacker'

import nltk
import codecs
import re
from topia.termextract import extract
import wiki_search
import os.path
import sys
import gensim
import time
import operator

from sklearn.cluster import KMeans, AffinityPropagation
from sklearn.metrics import pairwise
from scipy.spatial import distance
import numpy

from training import druid


def check_path(path):
    if not os.path.isfile(path):
        path = 'python/'+path
        if not os.path.isfile(path):
            print 'Could not find ', path, '!'
            sys.exit(-1)
    return path


def read_file(path):
    ami_file_name = check_path(path)

    with codecs.open(ami_file_name, 'r', 'utf-8') as in_file:
        return in_file.read()


def data_directory():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

w2v_model_path = check_path(os.path.join(data_directory(), 'enwiki-latest-pages-articles.word2vec'))
w2v_google_model_path = check_path(os.path.join(data_directory(), 'GoogleNews-vectors-negative300.bin.gz'))
tfidf_model_path = check_path(os.path.join(data_directory(), 'wiki.tfidf'))
tfidf_conversation_path = check_path(os.path.join(data_directory(), 'conversation.tfidf'))
druid_path = check_path(os.path.join(data_directory(), 'druid_en.bz2'))

# Filter hypens at the beginning and/or end of a word
# E.g. "schlechteste -> schlechteste
# There are many unicode hyphen variant (which all look very similar), we also want ot filter these
def filterHyphens(word):
    if word.startswith(u'"') or word.startswith(u"'") or word.startswith(u'"') or word.startswith(u'"') or word.startswith(u'"') or word.startswith(u'"'):
        word = word[1:]

    if word.endswith(u'"') or word.endswith(u"'") or word.endswith(u'"') or word.endswith(u'"') or word.endswith(u'"') or word.endswith(u'"'):
        word = word[:-1]

    return word


class W2VKeywordExtract:

    def __init__(self, lang='en', extra_keywords=''):
        self.lang = lang
        self.extra_keywords = extra_keywords
        self.keyword_extractor = extract.TermExtractor()
        self.keyword_extractor.filter = extract.permissiveFilter
        self.keyword_dict = {}

        self.common_words_filename = check_path('data/1-1000_en.txt')
        self.stopwords_filename = check_path('data/stopwords_en.txt')

        self.common_words = {}
        with codecs.open(self.common_words_filename, 'r', 'utf-8') as common_words_file:
            for line in common_words_file:
                self.common_words[line[:-1]] = 1

        self.stopwords = {}
        with codecs.open(self.stopwords_filename, 'r', 'utf-8') as stop_words_file:
            for line in stop_words_file:
                self.stopwords[line[:-1]] = 1

        # compiled regex that can check if a string contains numbers
        self.RE_D = re.compile('\d')

        self.start_time = time.time()

        self.stemmer = nltk.stem.PorterStemmer()
        self.lemmatizer = nltk.stem.WordNetLemmatizer()

        print 'Loading TF-IDF Model...'
        self.tfidf = gensim.models.tfidfmodel.TfidfModel.load(tfidf_model_path)
        self.tfidf_conversation = gensim.models.tfidfmodel.TfidfModel.load(tfidf_conversation_path)
        print 'Loading Word2Vec model... (this can take some time)'
        # self.word2vec = gensim.models.Word2Vec.load_word2vec_format(w2v_google_model_path, binary=True)
        self.word2vec = gensim.models.Word2Vec.load(w2v_model_path)
        self.druid = druid.DruidDictionary(druid_path, self.stopwords_filename, cutoff_score=0.2)

        print 'Time for loading models:', time.time() - self.start_time
        self.start_time = time.time()

    def preprocess_text(self, text):
        # Automatically tokenize strings if nessecary
        if type(text) is str or type(text) is unicode:
            tokens = nltk.word_tokenize(text.lower())

            # Apply a POS Tagger
            tags = nltk.pos_tag(tokens)

            # Remove stopwords (words that were too frequent in the corpus)
            # Filter nouns and adjectives. Dictionary is used to translate into WordNet Tags
            pos_pattern = ['NN', 'NNP', 'NNS', 'JJ']
            tag_filtered = [tag[0] for tag in tags if tag[1] in pos_pattern]
            # stop_filtered = [token for token in tag_filtered if token not in self.stopwords]
            idf_filtered = []
            for token in tag_filtered:
                try:
                    if token in self.stopwords:
                        continue
                    idf = self.tfidf.idfs[self.tfidf.id2word.token2id[self.stemmer.stem(token)]]
                    if idf > 3.67:
                        idf_filtered.append(token)
                except KeyError:
                    idf_filtered.append(token)

            ngrams = self.druid.find_ngrams(idf_filtered, n=3)

            lemmatized = [self.lemmatizer.lemmatize(token) for token in ngrams]

            print 'Time for preprocessing text:', time.time() - self.start_time

            return lemmatized
        return None

    def build_word_vector_matrix(self, words):
        numpy_arrays = []
        labels_array = []

        for token in words:
            try:
                vector = self.word2vec[self.stemmer.stem(token)]
                # vector = self.word2vec[token]
            except KeyError:
                continue

            labels_array.append(token)
            numpy_arrays.append(vector)

        return numpy.array(numpy_arrays), labels_array

    def compute_cluster_connectivity(self, cluster, cluster_center):
        num_words = len(cluster)
        if num_words == 1:
            return 0
        df, labels_array = self.build_word_vector_matrix(cluster)
        center_vector = self.word2vec[self.stemmer.stem(cluster_center)]
        # center_vector = self.word2vec[cluster_center]
        # center_vector = cluster_center
        total_distance = numpy.sum([distance.euclidean(vector, center_vector) for vector in df])

        return 1 / (total_distance / num_words)

    def compute_cluster_tfidf(self, cluster, tokens):
        score = 0
        num_words = len(cluster)

        for word in cluster:
            tf = len([token for token in tokens if word == token])

            try:
                token_id = self.tfidf.id2word.token2id[self.stemmer.stem(word)]
                idf = self.tfidf.idfs[token_id]
            except KeyError:
                # idf score is ignored for unknown words
                # (assumption: ASR noise, disadvantage: important terms need to be part of corpus)
                idf = 1.0

            score += tf * idf

        return score / num_words

    def get_cluster_score(self, cluster, cluster_center, tokens):
        connectivity_score = self.compute_cluster_connectivity(cluster, cluster_center)
        tfidf_score = self.compute_cluster_tfidf(cluster, tokens)

        print cluster
        print tfidf_score
        print connectivity_score

        return tfidf_score, connectivity_score

    # Assigns a score to each cluster, sorts them according to the score.
    # Returns a sorted clusters array with each cluster's corresponding score.
    def get_scored_clusters(self, clusters, cluster_centers, tokens):
        alpha = 1.0

        scores = [self.get_cluster_score(clusters[index], cluster_centers[index], tokens) for index in clusters]
        max_tfidf = max([score[0] for score in scores])
        max_connectivity = max([score[1] for score in scores])
        normalized_scores = [(score[0] / max_tfidf, score[1] / max_connectivity) for score in scores]
        # cluster_score = alpha * tfidf_norm + (1-alpha) * connectivity_norm
        # note: single-worded clusters have no connectivity score => penality for their score (assumption: off-topic)
        # cluster_scores = [(alpha * score[0] + (1-alpha) * score[1]) for score in normalized_scores]
        cluster_scores = [score[0] * score[1] for score in scores]

        scored_clusters = zip(clusters, cluster_scores)

        return [(clusters[score[0]], score[1]) for score in scored_clusters]

    # Build word clusters using K-Means++
    def get_kmeans_clusters(self, tokens):
        # Remove duplicates
        tokens = list(set(tokens))

        df, labels_array = self.build_word_vector_matrix(tokens)
        clusters_to_make = int(numpy.ceil(len(labels_array) / 4.0))
        kmeans_model = KMeans(init='k-means++', n_clusters=clusters_to_make, n_init=10)
        kmeans_model.fit(df)

        cluster_labels = kmeans_model.labels_
        cluster_centers = kmeans_model.cluster_centers_

        # Get cluster_word assignments by inverting word_cluster assignments
        # cluster_to_words  = find_word_clusters(labels_array, cluster_labels)
        word_centroid_map = dict(zip(labels_array, cluster_labels))
        centroid_word_map = {}
        for k, v in word_centroid_map.iteritems():
            centroid_word_map[v] = centroid_word_map.get(v, [])
            centroid_word_map[v].append(k)

        return centroid_word_map, cluster_centers

    def get_ap_clusters(self, tokens):
        tokens = list(set(tokens))
        df, labels_array = self.build_word_vector_matrix(tokens)

        sim_matrix = pairwise.pairwise_distances(df, metric='euclidean')

        af = AffinityPropagation(affinity='euclidean').fit(df)

        cluster_centers_indices = af.cluster_centers_indices_
        cluster_labels = af.labels_
        # Dictionary: cluster_id -> center_word
        cluster_centers = dict([(index, labels_array[cluster_centers_indices[index]])
                                for index in range(0, len(cluster_centers_indices))])

        n_clusters_ = len(cluster_centers_indices)

        word_centroid_map = dict(zip(labels_array, cluster_labels))
        centroid_word_map = {}
        for k, v in word_centroid_map.iteritems():
            centroid_word_map[v] = centroid_word_map.get(v, [])
            centroid_word_map[v].append(k)

        return centroid_word_map, cluster_centers


    # Scores a keyphrase according to its centrality within the weighted clusters.
    #
    def score_keyphrase(self, phrase, cluster_centers, cluster_scores, max_distance, text_tokens):
        try:
            phrase_vector = self.word2vec[self.stemmer.stem(phrase)]
            # phrase_vector = self.word2vec[phrase]
            center_vectors, labels = self.build_word_vector_matrix(cluster_centers.values())
            # center_vectors = cluster_centers
            total_distance_score = numpy.sum(
                [cluster_scores[index] * (1 - distance.euclidean(phrase_vector, center_vectors[index]) / max_distance)
                 for index in range(0, len(cluster_centers))])
        except KeyError:
            total_distance_score = 0

        tf = len([token for token in text_tokens if self.stemmer.stem(phrase) == self.stemmer.stem(token)])
        try:
            idf = self.tfidf.idfs[self.tfidf.id2word.token2id[self.stemmer.stem(phrase)]]
        except KeyError:
            idf = 1.0

        # distance_score = 1.0 / total_distance if total_distance > 0 else 0
        tfidf_score = tf * idf

        return total_distance_score, tfidf_score

    def get_sorted_keyphrases(self, text_tokens, cluster_centers, cluster_scores, alpha=0.8):
        tokens = list(set(text_tokens))

        token_vectors, token_labels = self.build_word_vector_matrix(tokens)
        center_vectors, center_labels = self.build_word_vector_matrix(cluster_centers.values())
        dist_matrix = pairwise.pairwise_distances(X=center_vectors, Y=token_vectors, metric='euclidean')
        # Maximum distance for any token to any cluster
        max_distance = numpy.max(dist_matrix)

        scores = [self.score_keyphrase(phrase, cluster_centers, cluster_scores, max_distance, text_tokens) for phrase in tokens]
        max_dist_score = max([score[0] for score in scores])
        max_tfidf_score = max([score[1] for score in scores])
        normalized_scores = [(score[0] / max_dist_score, score[1] / max_tfidf_score) for score in scores]
        # keyphrase_scores = [(alpha * score[0] + (1-alpha) * score[1]) for score in normalized_scores]
        keyphrase_scores = [(score[0] * score[1]) for score in scores]

        keyphrase_scores_sorted = sorted(zip(tokens, keyphrase_scores), key=lambda keyphrase: keyphrase[1])
        return keyphrase_scores_sorted


    # Habibi Diversity
    def subset_cluster_score(self, subset, center_vector, max_distance):
        subset_vectors, labels_array = self.build_word_vector_matrix(subset)

        if len(labels_array) == 0:
            return 0

        dist_matrix = pairwise.pairwise_distances(X=center_vector, Y=subset_vectors, metric='euclidean')
        proximity_scores = [(1 - vector_distance / max_distance) for vector_distance in dist_matrix[0]]
        avg_proximity_score = numpy.sum(proximity_scores) / len(labels_array)

        return avg_proximity_score

    def score_subset(self, subset, cluster_scores, center_vectors, max_distance, text_tokens):
        beta = 1.0

        tfidf_score = self.compute_cluster_tfidf(subset, text_tokens)
        centrality_score = numpy.sum(
            [cluster_scores[index] * self.subset_cluster_score(subset, center_vectors[index], max_distance)**beta
             for index in range(0, len(cluster_scores))])

        return centrality_score, tfidf_score

    def find_best_subset(self, text_tokens, n, cluster_centers, cluster_scores, beta=0.25):
        tokens = list(set(text_tokens))
        token_vectors, token_labels = self.build_word_vector_matrix(tokens)
        center_vectors, center_labels = self.build_word_vector_matrix(cluster_centers.values())
        # center_vectors = cluster_centers
        dist_matrix = pairwise.pairwise_distances(X=center_vectors, Y=token_vectors, metric='euclidean')
        # Maximum distance for any token to any cluster
        max_distance = numpy.max(dist_matrix)


        extracted_tokens = []
        extracted_scores = []

        while len(extracted_tokens) < n and len(tokens) > 0:
            scores = [self.score_subset(extracted_tokens + [token], cluster_scores, center_vectors, max_distance, text_tokens)
                      for token in tokens]
            # max_centrality = max(score[0] for score in scores)
            # max_tfidf = max(score[1] for score in scores)
            # normalized_scores = [(score[0] / max_centrality, score[1] / max_tfidf) for score in scores]
            # weighted_scores = [(beta * score[0] + (1 - beta) * score[1]) for score in normalized_scores]
            weighted_scores = [(score[0] * score[1]) for score in scores]
            max_index, max_value = max(enumerate(weighted_scores), key=operator.itemgetter(1))
            extracted_tokens.append(tokens[max_index])
            extracted_scores.append(max_value)
            del tokens[max_index]

        return zip(extracted_tokens, extracted_scores)



if __name__ == "__main__":
    print 'Scripting directly called, I will perform some testing.'
    ke = W2VKeywordExtract()

    ted_root_dir = os.path.join(data_directory(), 'ted_transcripts')
    for file in os.listdir(ted_root_dir):
        if file.endswith('.txt'):
            with codecs.open(os.path.join(ted_root_dir, file), 'r', encoding='utf-8', errors='replace') as in_file:
                print 'Processing', file, ':'

                raw = in_file.read()
                tokens = ke.preprocess_text(raw)

                print 'Text:'
                for sentence in nltk.sent_tokenize(raw):
                    print sentence
                print 'Tokens:', tokens

                ap_clusters_map, cluster_centers = ke.get_ap_clusters(tokens)
                # ap_clusters_map, cluster_centers = ke.get_kmeans_clusters(tokens)
                scored_clusters = ke.get_scored_clusters(ap_clusters_map, cluster_centers, tokens)
                cluster_scores = [cluster[1] for cluster in scored_clusters]
                sorted_keyphrases = ke.get_sorted_keyphrases(tokens, cluster_centers, cluster_scores)
                sorted_keyphrases_tfidf = ke.get_sorted_keyphrases(tokens, cluster_centers, cluster_scores, 0.0)
                habibi_subset = ke.find_best_subset(tokens, 9, cluster_centers, cluster_scores)
                print "Affinity Propagation:"
                print ap_clusters_map
                print cluster_centers
                print sorted(scored_clusters, key=lambda cluster: cluster[1])
                print "Keyphrases:"
                print sorted_keyphrases
                print "Keyphrases (TFIDF):"
                print sorted_keyphrases_tfidf
                print "Habibi:"
                print habibi_subset
