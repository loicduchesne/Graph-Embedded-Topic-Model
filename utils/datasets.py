# Imports
import os
import glob
import anndata
import numpy as np
import pandas as pd

from tqdm.notebook import tqdm

import torch
import torch_geometric as pyg
from torch_geometric.data import Data, HeteroData

class IBKHDataset:
    def __init__(self, data_dir: str):
        """
            Initialize the iBKH dataset.

            Args:
                data_dir (str): Directory where the data are located.
        """
        self.data_triples = None # Save

        self.data_dir = os.path.expanduser(data_dir)

        # Load vocab CSVs
        self.drug_vocab_df    = pd.read_csv(os.path.join(self.data_dir, 'drug_vocab.csv'))
        self.disease_vocab_df = pd.read_csv(os.path.join(self.data_dir, 'disease_vocab.csv'))
        self.gene_vocab_df    = pd.read_csv(os.path.join(self.data_dir, 'gene_vocab.csv'))

        # Map original ids (primary) to the new name.
        self.drug_conv = dict(zip(self.drug_vocab_df['primary'], self.drug_vocab_df['name']))
        self.disease_conv = dict(zip(self.disease_vocab_df['primary'], self.disease_vocab_df['icd_9']))
        self.gene_conv = dict(zip(self.gene_vocab_df['primary'], self.gene_vocab_df['symbol']))

        # Mapping dictionaries:
        # Maps the converted name to a unique integer index.
        self.drug2id = {name: idx for idx, name in enumerate(self.drug_vocab_df['name'])}
        self.disease2id = {name: idx for idx, name in enumerate(self.disease_vocab_df['icd_9'])}
        self.gene2id = {name: idx for idx, name in enumerate(self.gene_vocab_df['symbol'])}

        # Canonical-name converters (fixed CSV columns)
        self.drug_conv = dict(zip(self.drug_vocab_df['primary'],   self.drug_vocab_df['name']))
        self.disease_conv = dict(zip(self.disease_vocab_df['primary'], self.disease_vocab_df['icd_9']))
        self.gene_conv = dict(zip(self.gene_vocab_df['primary'],    self.gene_vocab_df['symbol']))

        # Data build_data
        self.global2type = []
        self.rel2id = {}
        self.triples = None

    def _rows_to_edges(self, df, src_conv, tgt_conv, src_map, tgt_map):
        df = df[df.iloc[:, 2] == 1].copy() # keep confirmed edges

        df['src'] = df.iloc[:, 0].map(src_conv)
        df['tgt'] = df.iloc[:, 1].map(tgt_conv)
        df = df.dropna(subset=['src', 'tgt'])

        df['src_id'] = df['src'].map(src_map)
        df['tgt_id'] = df['tgt'].map(tgt_map)
        df = df.dropna(subset=['src_id', 'tgt_id'])

        return torch.tensor(df['src_id'].values, dtype=torch.long), torch.tensor(df['tgt_id'].values, dtype=torch.long)

    def build_data(self):
        """
            Build the triple tensor graph needed for Knowledge Graph Embedding (KGE) models. This was designed for uses with the PyTorch-Geometric framework.

            Returns:
              torch_geometric.data.Data
                edge_index: [2, E]
                edge_type: [E]
                num_nodes, num_edge_types
        """
        offset = {}
        cursor = 0
        for ntype, df in [('drug', self.drug_vocab_df),
                          ('disease', self.disease_vocab_df),
                          ('gene', self.gene_vocab_df)]:
            offset[ntype] = cursor
            self.global2type.extend([(ntype, i) for i in range(len(df))])
            cursor += len(df)

        h, r, t = [], [], []

        def add_rel(filename, src_type, rel_name, dst_type,
                    src_conv, tgt_conv, src_map, tgt_map):
            src_ids, dst_ids = self._rows_to_edges(
                pd.read_csv(os.path.join(self.data_dir, f'{filename}_res.csv')),
                src_conv, tgt_conv, src_map, tgt_map)

            rid = self.rel2id.setdefault((src_type, rel_name, dst_type),
                                         len(self.rel2id))

            h.append(src_ids + offset[src_type])
            t.append(dst_ids + offset[dst_type])
            r.append(torch.full_like(src_ids, rid))

        relations = [
            ('D_D',   'drug',    'D_D',   'drug',
             self.drug_conv,    self.drug_conv,    self.drug2id,    self.drug2id),
            ('D_Di',  'drug',    'D_Di',  'disease',
             self.drug_conv,    self.disease_conv, self.drug2id,    self.disease2id),
            ('D_G',   'drug',    'D_G',   'gene',
             self.drug_conv,    self.gene_conv,    self.drug2id,    self.gene2id),
            ('Di_Di', 'disease', 'Di_Di', 'disease',
             self.disease_conv, self.disease_conv, self.disease2id, self.disease2id),
            ('Di_G',  'disease', 'Di_G',  'gene',
             self.disease_conv, self.gene_conv,    self.disease2id, self.gene2id),
            ('G_G',   'gene',    'G_G',   'gene',
             self.gene_conv,    self.gene_conv,    self.gene2id,    self.gene2id),
        ]

        for rel_cfg in tqdm(relations, desc='Building triples...', unit='relations'):
            add_rel(*rel_cfg)

        head = torch.cat(h)
        tail = torch.cat(t)
        rel  = torch.cat(r)

        assert head.max() < cursor and tail.max() < cursor, 'Global index overflow.'

        self.triples = Data(
            edge_index=torch.stack([head, tail]),
            edge_type=rel,
            num_nodes=cursor,
            num_edge_types=len(self.rel2id))

        return self.triples