#!/usr/bin/env python3
#
# Gauchian: GBA variant caller
# Copyright 2021 Illumina, Inc.
# All rights reserved.
#
# Author: Xiao Chen <xchen2@illumina.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#

import os
import sys
from collections import namedtuple
from pprint import pprint
dir_name = os.path.dirname(os.path.dirname(__file__))
sys.path.append(dir_name)
from depth_calling.haplotype import (
    extract_hap,
    extend_hap_5p,
    extend_hap_3p,
    group_haps,
    filter_hap,
    join_subblocks,
)
from depth_calling.phasing import Phasing


class PhasingGba(Phasing):
    assembled_haplotypes = namedtuple(
        "assembled_haplotypes", "full_haplotypes hap_5p hap_3p"
    )
    def __init__(self):
        super().__init__()
        self.variant_sites = [2, 3, 6, 7]
        self.trusted_hap_threshold = [
            "1111112111",
            "1121111111",
            "1112111111",
            "1111111221"
            ]
        self.trusted_hap_switch_point = [
            "1111112111",
            "1121111111",
            "1112111111",
            "1111111221"
            ]
        self.variant_names = [
            "A495P",
            "L483P",
            "D448H",
            "c.1263del",
        ]
        self.variant_names_in_one = ["c.1263del+RecTL"]
        # sites used to calculate depth/read counts
        self.flanking_site = {
            2: [[2, 3]],
            3: [[2, 3]],
            6: [[6, 7]],
            7: [[6, 7]]
            }

    def get_variants_gba(self, hap):
        """
        Get variant names given haplotype
        """
        variants = []
        for i in range(len(hap)):
            if hap[i] == '2' and i in self.variant_sites:
                variant_index = self.variant_sites.index(i)
                variants.append(self.variant_names[variant_index])
        if variants == ["A495P", "L483P"]:
            return ["RecNciI"]
        if variants == ["A495P", "L483P", "D448H"]:
            return ["RecTL"]
        if variants == ["A495P", "L483P", "D448H", "c.1263del"]:
            return ["c.1263del+RecTL"]
        return variants

    def assemble_haplotypes(self, debug=False):
        """
        Assembly haplotypes for exon9-11
        """
        # starting from left
        hap_count = extract_hap(self.haplotype_per_read, range(2, 4)) # only check read support for A495P and L483P haplotypes, output is like {'CG': [1, 1], 'CT': [1]}
        hap_count = filter_hap(hap_count) # TODO: make sure the hap has at least 2 read support, output is like {'CG': [1, 1]}
        haplotypes_to_extend = ["x" * 2 + a + "x" * 6 for a in hap_count] # output is like {'xxCGxxxxxx': [1, 1]}
        # Extend to 5p
        matching_haplotype_groups = group_haps(self.haplotype_per_read, haplotypes_to_extend)
        haplotypes_to_extend = extend_hap_5p(matching_haplotype_groups)
        # TODO: bug, need to remove the following two lines
        matching_haplotype_groups = group_haps(self.haplotype_per_read, haplotypes_to_extend)
        haplotypes_to_extend = extend_hap_5p(matching_haplotype_groups)
        # Extend to 3p
        n = 0
        while n < 4:
            n += 1
            if haplotypes_to_extend != []:
                matching_haplotype_groups = group_haps(self.haplotype_per_read, haplotypes_to_extend)
                haplotypes_to_extend = extend_hap_3p(matching_haplotype_groups)
                if debug is True:
                    pprint(matching_haplotype_groups)
                    print(n, haplotypes_to_extend)
            else:
                break
        hap_5p = haplotypes_to_extend
        # starting from right
        hap_count = extract_hap(self.haplotype_per_read, [6, 7])
        hap_count = filter_hap(hap_count)
        haplotypes_to_extend = ["x" * 6 + a + a[-1] + "x" for a in hap_count]
        # Extend to 3p
        matching_haplotype_groups = group_haps(self.haplotype_per_read, haplotypes_to_extend)
        haplotypes_to_extend = extend_hap_3p(matching_haplotype_groups)
        # Extend to 5p
        n = 0
        while n < 4:
            n += 1
            if haplotypes_to_extend != []:
                matching_haplotype_groups = group_haps(self.haplotype_per_read, haplotypes_to_extend)
                haplotypes_to_extend = extend_hap_5p(matching_haplotype_groups)
                if debug is True:
                    pprint(matching_haplotype_groups)
                    print(n, haplotypes_to_extend)
            else:
                break
        hap_3p = haplotypes_to_extend
        full_hap, dcount = join_subblocks(self.haplotype_per_read, hap_5p, hap_3p, 2, 8)

        return self.assembled_haplotypes(full_hap, hap_5p, hap_3p)

    def check_deletion_bp_in_gene(self, var_index_in_haps):
        """In case of a deletion, check whether the deletion breakpoint is within the gene"""
        for hap in var_index_in_haps:
            # switching from 2s to 1s
            if (
                self.total_cn < 4 and
                hap.count("21") == 1 and
                "12" not in hap and
                "x" not in hap
            ):
                self.deletion_bp_in_gene = True

    def assess_hap(self, hap, var_index_in_haps, full_haplotypes):
        """
        Assess whether a haplotype is a variant haplotype that makes the
        sample a carrier, i.e. having only one copy of the WT gene
        """
        fully_assembled, var_sites_haps = self.assess_assembled_haps(full_haplotypes)
        good_var_indices = var_index_in_haps[hap]
        # Scenario 1: variant haplotype exists and there is only one copy of
        # the WT gene, based on depth at defined flanking sites
        # use different thresholds for trusted haplotypes
        if (hap in self.trusted_hap_threshold and len(full_haplotypes) <= 4):
            found_variant_in_short_hap, block_result = self.get_depth_for_blocks(
                good_var_indices, cn_threshold=self.CN_likelihood_threshold_loose)
        else:
            found_variant_in_short_hap, block_result = self.get_depth_for_blocks(
                good_var_indices, cn_threshold=self.CN_likelihood_threshold)
        if found_variant_in_short_hap:
            self.is_carrier = True
        # Scenario 2: considering all haplotypes, there is only one haplotype
        # that has the gene base at all variat sites
        # not based on depth. Only consider total_cn=3
        elif len(var_index_in_haps) == 1 and fully_assembled is True:
            carrier_from_haps = self.carrier_from_assembled_haps(var_sites_haps)
            if carrier_from_haps is True:
                self.is_carrier = True
        # Scenario 3: check switching points not included in flanking_site
        # for trusted haplotypes or all-2s to all-1s transitions
        if self.is_carrier is not True:
            if (
                hap in self.trusted_hap_switch_point or
                (hap.count("12") == 1 and "21" not in hap) or
                (hap.count("21") == 1 and "12" not in hap)
            ):
                for switching_hap in ["12", "21"]:
                    if switching_hap in hap:
                        switch_point = hap.index(switching_hap)
                        switch_block = self.get_depth_for_block(
                            [switch_point, switch_point + 1], switching_hap.index('1')
                            )
                        if switch_block not in block_result:
                            block_result.append(switch_block)
                        if (
                            switch_block.likelihood is not None and
                            switch_block.likelihood > self.CN_likelihood_threshold
                        ):
                            self.is_carrier = True
        if self.is_carrier is True:
            var_per_hap = self.var_call_per_haplotype(
                hap,
                self.get_variants_gba(hap),
                [a._asdict() for a in block_result],
                True
                )
        else:
            var_per_hap = self.var_call_per_haplotype(
                hap,
                self.get_variants_gba(hap),
                [a._asdict() for a in block_result],
                found_variant_in_short_hap
                )
        self.variants_details.append(var_per_hap)
