from django.core.management.base import LabelCommand
from django.db import transaction
from django.db.utils import IntegrityError
from datetime import datetime
from refinery.refinery_repository.models import *
import csv, sys, re, string, os, glob, traceback
from collections import defaultdict
from django.db import connection
from django.conf import settings
from file_store.tasks import create


class Command(LabelCommand):
    
    help = "Takes the directory of an ISA-Tab file as input, parses, and"
    help = "%s inputs it into the database" % help
    
    args = '[isatab_directory ...]'

    #headers in investigation files and internal abbreviations
    investigation_headers = {
                                'ONTOLOGY SOURCE REFERENCE': 'ont',
                                'INVESTIGATION': 'inv',
                                'INVESTIGATION PUBLICATIONS': 'inv_pub',
                                'INVESTIGATION CONTACTS': 'inv_tor',
                                'STUDY': 'tion',
                                'STUDY DESIGN DESCRIPTORS': 'sdd',
                                'STUDY PUBLICATIONS': 'pub',
                                'STUDY FACTORS': 'sf',
                                'STUDY ASSAYS': 'sa',
                                'STUDY PROTOCOLS': 'prot',
                                'STUDY CONTACTS': 'tor'
                             }

    #sub-header that is the most important for the given section
    key_terms = { 
                    'ont': 'term_source_name',
                    'inv': 'investigation_identifier',
                    'inv_pub': 'investigation_pubmed_id',
                    'inv_tor': 'investigation_person_email',
                    'tor': 'study_person_email',
                    'sdd': 'study_design_type',
                    'sf': 'study_factor_name',
                    'sa': 'study_assay_file_name',
                    'pub': 'study_pubmed_id',
                    'prot': 'study_protocol_name',
                    'tion': 'study_identifier'
                }
    
    #terms that can be substituted in for the standard ones
    #substitute: standard
    sub_terms = {
                 'Hybridization Assay Name': 'Assay Name',
                 'Derived Array Data Matrix File': 'Derived Data File',
                 'Comment [Derived ArrayExpress FTP file]': 'Derived ArrayExpress FTP file',
                 'Comment[ArrayExpress FTP file]': 'Raw Data File',
                 'Comment[FASTQ_URI]': 'Raw Data File',
                 'Array Data File': 'Raw Data File',
                 'Derived Array Data File': 'Derived Data File',
                 'Array Data Matrix File': 'Raw Data File'
                 }
    
    node_names = ['Source Name', 'Sample Name', 'Extract Name',
                  'Labeled Extract Name', 'Assay Name', 'Image File',
                  'Raw Data File', 'Data Transformation Name', 
                  'Normalization Name', 'Derived Data File']
    
    dictionary = defaultdict(list)

    
    def get_subtype(self, field):
        """
        Name: get_subtype
        Description:
            extracts the sub-type from the Characteristic or Factor Value
            header
        Parameters:
            field: the text from the header field that's being parsed
        """
        left_bracket = string.index(field, '[')
        right_bracket = string.rindex(field, ']')
        """get the positions of the [] that surround the sub-type"""

        subtype = field[left_bracket+1:right_bracket].upper()
        """get everything between [] and uppercase it"""
        
        sub_type = string.join(string.split(subtype, ' '), '_')
        """substitute spaces with underscores"""
        
        return sub_type
        """return the sub-type""" 

    def organize_investigation_info(self, invest_info):
        """
        Name: organize_investigation_info
        Description:
            organizes all of the information into one dictionary and 
            returns it
        Parameter:
            invest_info: dictionary of dictionary of lists that contains the 
                         current compendium of information gleaned from the 
                         investigation file
        """
        investigation = defaultdict(list)
        
        #v is dict of lists e.g. {'address': ['', ''], 'phone': ['', '']}
        for k, v in invest_info.items():
            #column indexes that have information to grab for each section
            indexes = list() 
            for i, val in enumerate(v[self.key_terms[k]]):
                if not re.search(r'^\s*$', val): #make sure val not empty
                    indexes.append(i)
                    
            #for every column that has information to insert
            for i in indexes:
                temp_dict = dict()
                #change the key if inv_*, also means inv_key needs to change
                change_key = string.find(k, '_')
                
                #inv_key (e.g. study_person_fax, study_protocol_name)
                for inv_key, inv_list in v.items():
                    try:
                        if change_key < 0:
                            temp_dict[inv_key] = inv_list[i]
                        else:
                            new_inv_key = string.replace(inv_key, 'investigation', 'study')
                            temp_dict[new_inv_key] = inv_list[i]
                    except IndexError:
                        pass
                 
                if change_key < 0: #didn't have an '_' in the key name
                    investigation[k].append(temp_dict)
                else:
                    new_key = k[change_key+1:] #grab everything after '_'
                    investigation[new_key].append(temp_dict)
                    
        #edit the contents of 'tion' to include the contents of 'inv'
        for k, v in invest_info['inv'].items():
            try:
                #there will only be one entry in 'tion' and v
                investigation['tion'][0][k] = v[0]
            except IndexError:
                pass
                    
        #reset the blocks that can be duplicated
        invest_info['tor'] = defaultdict(list)
        invest_info['tion'] = defaultdict(list)
        invest_info['sdd'] = defaultdict(list)
        invest_info['sf'] = defaultdict(list)
        invest_info['sa'] = defaultdict(list)
        invest_info['pub'] = defaultdict(list)
        invest_info['prot'] = defaultdict(list)
        
        return investigation

    """
    Name: parse_investigation_file
    Description:
        parse the fields relevant to our Django model and put them into a
        dictionary
    Parameters:
        i_file: path to investigation file
    """
    def parse_investigation_file(self, i_file):
        invest_info = {
                       'tor': defaultdict(list), #investigator
                       'tion': defaultdict(list), #investigation
                       'inv': defaultdict(list), #actual isatab investigation
                       'inv_pub': defaultdict(list), #investigation publication
                       'inv_tor': defaultdict(list), #investigation investigator
                       'ont': defaultdict(list), #ontology
                       'sdd': defaultdict(list), #study design descriptor
                       'sf': defaultdict(list), #study factor
                       'sa': defaultdict(list), #study assay
                       'prot': defaultdict(list), #protocol
                       'pub': defaultdict(list) #publication
                       }
        investigation = list() #object to return, list of dictionaries
        
        #read in investigation file
        file_reader = open(i_file, 'rb')
        
        #grab the investigation file values
        current_header = ""
        for row in file_reader:
            #list comprehension that splits on tabs and strips each
            #element of the array of flanking whitespace
            fields = [x.strip() for x in string.split(row, '\t')]
            #if all caps, designate the dictionary the information will go into
            if re.search(r'[A-Z]{4}', fields[0]): #a section header
                current_header = self.investigation_headers[fields[0]]
            else: #if an information row, then push the information into the dictionary
                #database columns are the header names, made lower
                #case and joined by "_" (e.g. study_title)
                name = fields.pop(0).lower()

                #remove surrounding "s
                fields = [x.strip(r'"') for x in fields]

                db_col = string.join(string.split(name, ' '), '_')
                if(db_col):
                    #if there are multiple study blocks, then organize the 
                    #previous block and store it, then move along
                    if invest_info[current_header][db_col]:
                        invest = self.organize_investigation_info(invest_info)
                        investigation.append(invest)

                    for f in fields:
                        invest_info[current_header][db_col].append(f)
 
        #last/only investigation
        oii = self.organize_investigation_info(invest_info)
        investigation.append(oii)

        for investigation_dict in investigation:
            try: #delete 'inv' if it's there
                del investigation_dict['inv'] #information is in 'tion', so delete
            except KeyError:
                pass

            """
            for k, v in investigation_dict.items():
                print "%s:" % k,
                print v
                print
            print '================================='
            """
        
        return investigation

    def parse_header_row(self, header_row):
        header = dict()
        cp = 0 #most recent protocol
        cpp = 0 #most recent protocol performer
        cpd = 0 #most recent protocol date
        cbi = 0 #most recent bracketed field index
        cnn = '' #current node name (listed in global node_names dictionary)
        cn = 0 #current node index

        for i, j in enumerate(header_row):
            """
            revert fields that have different names due to technology type
            back to their original ones for database storage
            """
            if j in self.sub_terms:
                j = self.sub_terms[j]

            """make sure there aren't any words stuck together on accident"""
            #there aren't spaces between words & not ArrayExpress
            if not (re.search('ArrayExpress', j) or re.search(r'\[', j)):
                if re.search(r'[A-Z][a-z]+[A-Z][a-z]+', j):
                    #print j
                    s = list(j) #convert string to a list
                    for x, y in enumerate(s): #for every character
                        #if a capital letter, prepend space
                        if re.search(r'[A-Z]', y): 
                            s[x] = " %s" % y
                    j = string.join(s, '').strip()

            """ get current node name and index """
            if j in self.node_names:
                cn = i
                cnn = j

            """
            mark term refs, term accessions, protocols, and units with the 
            bracketed header (characteristic, factor value, or parameter 
            value) index so they can be put together in one model
            """
            if re.search(r'Term Source', j):
                if re.search(r'Unit', header[i-1]):
                    header[i] = "%s\tUnit Term Source REF" % cbi
                elif re.search(r'\[', header[i-1]):
                    header[i] = "%s\t%s" % (str(i - 1), j)
                else:
                    header[i] = "%s %s" % (header[i-1], j)
            elif re.search(r'Term Accession', j):
                if re.search(r'Unit', header[i-2]):
                    header[i] = "%s\tUnit Term Accession Number" % cbi
                elif re.search(r'\[', header[i-2]):
                    header[i] = "%s\t%s" % (str(i-2), j)
                else:
                    header[i] = "%s %s" % (header[i-2], j)
            elif re.search(r'Unit', j):
                #ArrayExpress imported studies have Unit [*]; make it so
                #it's just 'Unit'
                header[i] = "%s\tUnit" % cbi
            else:
                header[i] = j
                if re.search(r'\[', j):
                    cbi = i
                    if re.search(r'Parameter', j):
                        if cp:
                            header[i] = "%s\t" % cp
                        if cpp:
                            header[i] += "%s\t" % cpp
                        if cpd:
                            header[i] += "%s\t" % cpd
                        header[i] += j
                    header[i] += "\t%d\t%s" % (cn, cnn)
                elif re.search(r'Data Transformation', j):
                    if cp:
                        header[i] = "%s\t" % cp
                    if cpp:
                        header[i] += "%s\t" % cpp
                    if cpd:
                        header[i] += "%s\t" % cpd
                    header[i] += j
                elif re.search(r'Material', j):
                    if re.search(r'Extract', cnn):
                        header[i] = "%s %s" % (cnn, j)
                elif re.search(r'Protocol', j):
                    cp = i
                    cpp = 0
                    cpd = 0
                elif re.search(r'Performer', j):
                    cpp = i
                elif re.search(r'Date', j):
                    cpd = i

        #print 'header:'
        #print header
        #print
        return header
    
    def get_bracketed_info(self, row, header_dict, index):
        #there are tabs, so split up the string to get
        #the current node and its name
        split_string = string.split(header_dict[index], "\t")
        #print split_string
        
        #grab the node name, node index, and actual header text
        node_name = split_string.pop()
        node = split_string.pop()
        header = split_string.pop()
        
        """
        return everything before '[', lowercase it, substitute ' ' with '_'
        in order to create the key (characteristics, factor_value, etc)
        """
        key = string.split(header, '[').pop(0).lower().strip()
        key_parts = string.split(key, ' ')
        key = string.join(key_parts, '_')
        
        """get the stuff in the brackets as the sub-key"""
        sub_key = self.get_subtype(header)
        
        #assign values
        try:
            self.dictionary['b'][key][sub_key]['sub_type'] = sub_key
        except KeyError:
            try:
                self.dictionary['b'][key][sub_key] = dict()
            except KeyError:
                self.dictionary['b'][key] = dict()
                self.dictionary['b'][key][sub_key] = dict()
            self.dictionary['b'][key][sub_key]['sub_type'] = sub_key

        self.dictionary['b'][key][sub_key]['type'] = key
        self.dictionary['b'][key][sub_key]['value'] = row[index]
        self.dictionary['b'][key][sub_key]['node_name'] = node_name
        self.dictionary['b'][key][sub_key]['node_index'] = node
        
        #if parameter value, there will be more things to grab
        #index of associated protocol if parameter value
        for i in split_string:
            ind = int(i) #index of thing
            #get the key name, protocol, performer, or date
            key_name = string.split(header_dict[ind], ' ').pop(0).lower()
            #assign
            self.dictionary['b'][key][sub_key][key_name] = row[ind]
        #print 'from get_bracketed_info'
        #print '[%s][%s] = %s' % (key, sub_key, self.dictionary['b'][key][sub_key])

    def lab(self, header_string, field, current_header):
        sub_type = self.get_subtype(header_string)

        #key is the key for study_info
        key = string.split(header_string, '[').pop(0).lower().strip()
        key = re.sub(r' ', r'_', key)
        if re.search(r'[0-9]+', key):
            key_parts = string.split(key, '\t')
            key = key_parts.pop()

        #field header
        sub_key = re.sub(r' ', r'_', current_header).lower()

        #print 'from lab'
        #print 'dictionary[b][%s][%s][%s] = %s' % (key, sub_type, sub_key, field)
        self.dictionary['b'][key][sub_type][sub_key] = field

    """
    Name: parse_study_file
    Description:
        parse the fields relevant to our Django model and put them into a
        dictionary
    Parameters:
        s_file: path to study file
    """
    def parse_study_file(self, s_file):
        #dictionary of dictionary of lists
        study_info = {
                      'study': list(),
                      'studybracketedfield': list(),
                      'protocol': list()
                      }
        
        #read in study file
        file_reader = csv.reader(open(s_file, 'rb'), dialect='excel-tab')

        #grab first row to get field headers
        header_row = file_reader.next()
        #dictionary that correlates column index and header text
        header = self.parse_header_row(header_row)

        #iterate over the file
        for i, row in enumerate(file_reader):
            
            #some data structures for temporary insertion
            protocols = list()
            self.dictionary = defaultdict(dict)

            for j, field in enumerate(row):
                if not re.search(r'^\s*$', field):
                    #comment or characteristic
                    if re.search(r"\[", header[j]):
                        self.get_bracketed_info(row, header, j)   
                    else:
                        #get name of the header with '_' substituted for ' ' and lowercase
                        key_parts = [x.lower().strip() for x in string.split(header[j], ' ')]
                        key = string.join(key_parts, '_')
                    
                        if re.search(r'^Protocol ', header[j]):
                            protocols.append(field)
                        elif re.search(r'^[0-9]+', header[j]):
                            #isolate index of corresponding characteristic
                            #and prepare to substitute underscores for spaces
                            split = string.split(header[j], '\t')
                            
                            #get Characteristics[something]
                            char = header[int(split.pop(0))]
                            self.lab(char, field, split.pop())
                            
                        else:
                            self.dictionary['s'][key] = field
            #append list of protocol lists
            study_info['protocol'].append(protocols)
            
            #assign row number to end of dicts so we know what's together
            #and append them to the study_info
            self.dictionary['s']['row_num'] = i
            study_info['study'].append(self.dictionary['s'])
            #organize bracketed items into proper categories
            for d in self.dictionary['b']:
                for k in self.dictionary['b'][d]:
                    temp = self.dictionary['b'][d][k]
                    temp['row_num'] = i
                    study_info['studybracketedfield'].append(temp)

        """
        print '----------------------------'
        print 'study'
        print study_info['study']
        print
        print 'studybracketedfield'
        print study_info['studybracketedfield']
        print
        print 'protocol'
        print study_info['protocol']
        print '----------------------------'
        print
        """

        return study_info
           
    """
    Name: parse_assay_file
    Description:
        parse the fields relevant to our Django model and put them into a
        dictionary
    Parameters:
        a_file: path to assay file
        accession: associated investigation study identifier
    """
    def parse_assay_file(self, a_file, accession):
        assay_info = {
                      'raw_data': list(),
                      'processed_data': list(),
                      'protocol': list(),
                      'assaybracketedfield': list(),
                      'datatransformation': list(),
                      'assay': list()
                      }
        #read in assay file, can't use dictionary because keys may be 
        #potentially overwritten
        file_reader = csv.reader(open(a_file, 'rb'), dialect='excel-tab')

        #grab first row to get field headers
        header_row = file_reader.next()
        #dictionary that correlates column index and header text
        header = self.parse_header_row(header_row)


        for i, row in enumerate(file_reader):
            #initialize data structures
            protocols = list()
            self.dictionary = defaultdict(list)
            self.dictionary['a'] = dict()
            self.dictionary['b'] = dict()

            for j, field in enumerate(row):
                if not re.search(r'^\s*$', field): #if not empty
                    if re.search(r'^Raw Data', header[j]):
                        temp_dict = dict()
                        if re.search(r'fastq$', field):
                            field += ".gz"

                        temp_dict['raw_data_file'] = field
                        #raw.x.zip file
                        if re.search(r'^http', field) or re.search(r'^ftp', field):
                            index = string.rindex(field, '/')
                            temp_dict['file_name'] = field[index+1:]
                        else: #anything else, file name & raw the same
                            temp_dict['file_name'] = field

                        self.dictionary['r'].append(temp_dict)
                    elif re.search(r'\[.+\]', header[j]):
                        self.get_bracketed_info(row, header, j)
                    else:
                        if re.search(r'[0-9]+', header[j]):
                            header_info = string.split(header[j], '\t')
                            current_header = header_info.pop()

                            #get Factor Value[something]
                            fv = header[int(header_info[0])]

                            if re.search(r'\[', fv):
                                self.lab(fv, field, current_header)
                            else:
                                temp_dict = dict()
                                current_header = current_header.lower()
                                current_header = re.sub(r' ', r'_', current_header)
                                temp_dict[current_header] = field
                                #if parameter value, there will be more things to grab
                                #index of associated protocol if parameter value
                                for index in header_info:
                                    ind = int(index) #index of thing
                                    #get the key name, protocol, performer, or date
                                    key_name = string.split(header[ind], ' ').pop(0).lower()
                                    #assign
                                    temp_dict[key_name] = row[ind]
                                self.dictionary['d'].append(temp_dict)
                        else:
                            #get name of the header with '_' substituted for ' '
                            #and lowercase
                            key_parts = [x.lower().strip() for x in string.split(header[j], ' ')]
                            key = string.join(key_parts, '_')

                            if re.search(r'^Derived', header[j]):
                                try:
                                    temp_dict = self.dictionary['p'].pop()
                                except:
                                    temp_dict = dict()
                                if key in temp_dict:
                                    self.dictionary['p'].append(temp_dict)
                                    temp_dict = dict()
                                
                                if re.search(r'ArrayExpress', header[j]):
                                    temp_field = temp_dict['derived_data_file']
                                    temp_dict['derived_data_file'] = field
                                    temp_dict['file_name'] = temp_field
                                else:
                                    temp_dict[key] = field
                                self.dictionary['p'].append(temp_dict)
                            elif re.search(r'^Protocol REF', header[j]):
                                protocols.append(field)
                            else:
                                self.dictionary['a'][key] = field

            assay_info['protocol'].append(protocols)

            if self.dictionary['r']:
                #assign row number to dict so we know what's together
                for r in self.dictionary['r']:
                    r['row_num'] = i
                #print dictionary['r']
                
                assay_info['raw_data'].append(self.dictionary['r'])

            if self.dictionary['a']:
                #assign row number to dict so we know what's together
                self.dictionary['a']['row_num'] = i
                assay_info['assay'].append(self.dictionary['a'])

            if self.dictionary['p']:
                for p in self.dictionary['p']:
                    #assign row number to dict so we know what's together
                    p['row_num'] = i

                assay_info['processed_data'].append(self.dictionary['p'])

            if self.dictionary['d']:
                for d in self.dictionary['d']:
                    #assign row number to dict so we know what's together
                    d['row_num'] = i
                assay_info['datatransformation'].append(self.dictionary['d'])
            
            #can't iterate an int, so delete and re-add later
            try:
                #organize bracketed items into proper categories
                for d in self.dictionary['b']:
                    for k in self.dictionary['b'][d]:
                        temp = self.dictionary['b'][d][k]
                        temp['row_num'] = i
                        assay_info['assaybracketedfield'].append(temp)
            except KeyError: #no bracketed information
                pass

        """
        print '----------------------------'
        print 'assay'
        print assay_info['assay']
        print
        print 'assaybracketedfield'
        print assay_info['assaybracketedfield']
        print
        print 'protocol'
        print assay_info['protocol']
        print
        print 'raw_data'
        print assay_info['raw_data']
        print
        print 'processed_data'
        print assay_info['processed_data']
        print
        print 'datatransformation'
        print assay_info['datatransformation']
        print '----------------------------'
        print
        """
        
        return assay_info

    """
    Name: insert_isatab
    Description:
        insert all of the ISA-Tab information or bust
    Parameters:
        i_list: list of dictionaries of investigation file
        isatab: location of zipped ISA-Tab
        pre_isatab: location of zipped files that were converted into
                    ISA-Tab (may or may not exist)
        stud_list: list of dictionaries of study file(s)
        assay_dictionary: dictionary of lists of assay file(s); dictionary 
                          key is the investigation accession
    """
    def insert_isatab(self, i_list, isatab, pre_isatab, stud_list, assay_dictionary):
        #keep track of every publication, raw data, and processed data object
        #that is inserted in to make it easy to delete them if something 
        #goes wrong
        publications_master_list = list()
        raw_data_master_list = list()
        processed_data_master_list = list()

        #try:
        for ind, i_dict in enumerate(i_list):
            tor_list = i_dict['tor'] #investigator
            """
            print 'tor_list'
            print tor_list
            print
            """
            tion_dict = i_dict['tion'][0] #investigation
            """
            print 'tion_dict'
            print tion_dict
            print
            """
            ont_list = i_dict['ont'] #ontology
            """
            print 'ont_list'
            print ont_list
            print
            """
            prot_list = i_dict['prot'] #protocols
            """
            print 'prot_list'
            print prot_list
            print
            """
            sdd_list = i_dict['sdd'] #study design descriptors
            """
            print 'sdd_list'
            print sdd_list
            print
            """
            sf_list = i_dict['sf'] #study factors
            """
            print 'sf_list'
            print sf_list
            print
            """
            pub_list = i_dict['pub'] #publications
            """
            print 'pub_list'
            print pub_list
            print
            """
            sa_list = i_dict['sa'] #study assays
            """
            print 'sa_list'
            print sa_list
            print
            """

            # "**" converts dictionary to arguments
            #make sure dates are datetime.date objects
            for k, v in tion_dict.items():
                if re.search(r'_date', k):
                    try:
                        the_date = datetime.strptime(v, '%Y-%m-%d').date()
                        tion_dict[k] = the_date
                    except ValueError:
                        del tion_dict[k]
                        
            #add in pre_isatab_file and isatab_file
            tion_dict['isatab_file'] = isatab
            tion_dict['pre_isatab_file'] = pre_isatab

            investigation = Investigation(**tion_dict)
            investigation.save()

            for tor_dict in tor_list:
                investigator = Investigator(**tor_dict)
                investigator.save()
                
                #add investigation to investigator
                investigator.investigations.add(investigation)

            #add investigation to ont dictionary and insert ontologies
            for ont_dict in ont_list:
                #ont_dict['investigation'] = investigation
                ontology = Ontology(**ont_dict)
                #print ontology
                try:
                    ontology.save()
                except:
                    connection._rollback()
                    name = ont_dict['term_source_name']
                    try:
                        file = ont_dict['term_source_file']
                    except KeyError:
                        file = ''
                    try:
                        ver = ont_dict['term_source_version']
                    except KeyError:
                        ver = ''
                
                    ontology = Ontology.objects.get(term_source_name=name,
                                                term_source_file=file,
                                                term_source_version=ver)

                investigation.ontologies.add(ontology)

            #add investigation to pub dictionary and insert publication(s)
            for pub_dict in pub_list:
                #sometimes 'Study Publication DOI DOI'; fix
                if 'study_publication_doi_doi' in pub_dict:
                    spd = pub_dict['study_publication_doi_doi']
                    del pub_dict['study_publication_doi_doi']
                    pub_dict['study_publication_doi'] = spd
            
                publication = Publication(**pub_dict)
                publication.save()
                
                #add investigation to publication
                publication.investigation_set.add(investigation)
                #add to master list
                publications_master_list.append(publication)

            #add investigation to sa dictionary and insert study assay(s)
            for sa_dict in sa_list:
                #using foreign key, so need to assign
                sa_dict['investigation'] = investigation
                
                sa = StudyAssay(**sa_dict)
                sa.save()

            #add investigation to prot dictionary and insert protocol(s)
            for prot_dict in prot_list:
                #print prot_dict
                protocol = Protocol(**prot_dict)
                #print prot_dict
                protocol.save()
                
                #add investigation to protocol
                protocol.investigation_set.add(investigation)
                #print 'protocol_saved'
        
            #get a dictionary of possible protocol names in the studies
            #and assays so it's easier to associate them to the originals
            protocol_list = investigation.protocols.all()
            protocols = dict()
            for p in protocol_list:
                name = p.study_protocol_name
                protocols[name] = p
                #create an abbreviated name
                #get the number on the end of the full protocol name
                number = string.split(name, '-').pop()
                abbr = "P--%s" % number
                protocols[abbr] = p

            #add investigation to sdd dictionary and insert 
            #study design descriptor(s)
            for sdd_dict in sdd_list:
                #add investigation as Foreign Key
                sdd_dict['investigation'] = investigation
                #create StudyDesignDescriptor
                sdd = StudyDesignDescriptor(**sdd_dict)
                sdd.save()

            #add investigation to prot dictionary and insert protocol(s)
            for sf_dict in sf_list:
                #add investigation as Foreign Key
                sf_dict['investigation'] = investigation
                #create StudyFactor
                sf = StudyFactor(**sf_dict)
                sf.save()
                
            """Studies"""
            sbf_list = stud_list[ind]['studybracketedfield']
            """
            print 'sbf_list'
            print sbf_list
            print
            """
            
            study_list = stud_list[ind]['study']
            """
            print 'study_list'
            print study_list
            print
            """
            prot_list = stud_list[ind]['protocol']
            """
            print 'prot_list'
            print prot_list
            print '----------------------------------'
            """
        
            
            #list of studies entered, needs to be returned
            s_list = list()
            #row_num: study pairs, makes it easy to associate other models
            #to the proper study
            study_dict = dict()
        
            for s in study_list:
                #remove row number from the dictionary
                row_num = s['row_num']
                del s['row_num']
            
                #grab associated investigation
                s['investigation'] = investigation
                #create study 
                study = Study(**s)
                study.save()
            
                #add to study_dict for the other models to use
                study_dict[row_num] = study
                #add to list for returning
                s_list.append(study)
        
            #insert protocols
            for i, p in enumerate(prot_list):
                s = study_dict[i]
                for prot in p:
                    try:
                        s.protocols.add(protocols[prot])
                    except KeyError:
                        protocol = Protocol(study_protocol_name=prot)
                        protocol.save()
                        s.protocols.add(protocol)
                        #add to hash for future look up 
                        protocols[prot] = protocol

            #insert StudyBracketedFields
            for sbf in sbf_list:
                row_num = sbf['row_num']
                del sbf['row_num']
            
                #grab asssociated study
                study = study_dict[row_num]
                sbf['study'] = study
                
                #if a parameter value (protocol field not empty), 
                #associate the proper protocol
                if 'protocol' in sbf:
                    sbf['protocol'] = protocols[sbf['protocol']]
                #create StudyBracketedField
                study_bracketed_field = StudyBracketedField(**sbf)
                study_bracketed_field.save()
            

            """Assays"""
            for a_dict in assay_dictionary[tion_dict['study_identifier']]:
                assay_list = a_dict['assay']
                """
                print 'assay_list'
                print assay_list
                print
                """
                raw_list = a_dict['raw_data']
                """
                print 'raw_list'
                print raw_list
                print
                """
                processed_list = a_dict['processed_data']
                """
                print 'processed_list'
                print processed_list
                print
                """
                abf_list = a_dict['assaybracketedfield']
                """
                print 'abf_list'
                print abf_list
                print
                """
                prot_list = a_dict['protocol']
                """
                print 'prot_list'
                print prot_list
                print
                """
                datatransform_list = a_dict['datatransformation']
                """
                print 'datatransform_list'
                print datatransform_list
                print
                print '============================'
                """
                #make study list a study dictionary instead so it's easier for
                #assays to find their associated studies
                study_dict = dict()
                for s in s_list:
                    study_dict[s.sample_name] = s
            
                assay_dict = dict()
                for a in assay_list:
                    #remove row number from the dictionary
                    row_num = a['row_num']
                    del a['row_num']
                
                    #grab associated study and investigation
                    study = study_dict[a['sample_name']]
                    a['study'] = study
                    a['investigation'] = investigation
                    #create assay 
                    assay = Assay(**a)
                    assay.save()
                
                    #add to assay_dict for the other models to use
                    assay_dict[row_num] = assay
                

                """ Many to Manys """
                for r_list in raw_list:
                    for r in r_list:
                        row_num = r['row_num']
                        del r['row_num']
                
                        #grab asssociated assay
                        assay = assay_dict[row_num]
                        
                        #grab rawdata_uuid from file store
                        r['rawdata_uuid'] = create(r['raw_data_file'])
                        #print r

                        #create RawData
                        raw_data = RawData(**r)
                        try:
                            raw_data.save()
                        except IntegrityError: #if already exists, grab
                            connection._rollback()
                            raw_data = RawData.objects.get(**r)
                        
                        #associate the assay
                        raw_data.assay_set.add(assay)
                        raw_data_master_list.append(raw_data)

                for p_list in processed_list:
                    for p in p_list:
                        row_num = p['row_num']
                        del p['row_num']
                
                        #grab asssociated assay
                        assay = assay_dict[row_num]
                
                        #create ProcessedData
                        p['processeddata_uuid'] = create(p['derived_data_file'])
                        processed_data = ProcessedData(**p)
                        try:
                            processed_data.save()
                        except IntegrityError: #if already exists, grab
                            connection._rollback()
                            processed_data = ProcessedData.objects.get(**p)
                
                        #associate the assay
                        processed_data.assay_set.add(assay)
                        processed_data_master_list.append(processed_data)
                
                #insert protocols
                for i, p in enumerate(prot_list):
                    a = assay_dict[i]
                    for prot in p:
                        try:
                            a.protocols.add(protocols[prot])
                        except KeyError:
                            protocol = Protocol(study_protocol_name=prot)
                            protocol.save()
                            a.protocols.add(protocol)
                            #add to hash for future look up 
                            protocols[prot] = protocol
                
                #print '\n assay bracketed field \n'
                for abf in abf_list:
                    row_num = abf['row_num']
                    del abf['row_num']
    
                    #grab asssociated assay
                    assay = assay_dict[row_num]
                    abf['assay'] = assay
                
                    #if a parameter value (protocol field not empty), 
                    #associate the proper protocol
                    if 'protocol' in abf:
                        abf['protocol'] = protocols[abf['protocol']]
                    #create AssayBracketedField
                    assay_bracket_field = AssayBracketedField(**abf)
                    assay_bracket_field.save()

                for dt_list in datatransform_list:
                    for dt in dt_list:
                        row_num = dt['row_num']
                        del dt['row_num']
                        
                        #grab associated assay
                        dt['assay'] = assay_dict[row_num]
                        #get associated protocol
                        dt['protocol'] = protocols[dt['protocol']]
                        
                        data_transformation = DataTransformation(**dt)
                        data_transformation.save()
                    
                
        #except:
            #error reporting for now
            """
            print "error: Investigation %s" % investigation.study_identifier
            exc_type, exc_value, exc_traceback = sys.exc_info()
            print "*** print_tb:"
            traceback.print_tb(exc_traceback, limit=1, file=sys.stdout)
            print "*** print_exception:"
            traceback.print_exception(exc_type, exc_value, exc_traceback,
                          limit=2, file=sys.stdout)
            
            #if there's an error, delete everything you put in
            #since there was an error, rollback so the rest works
            connection._rollback()
            for inv in i_list:
                accession = inv['tion']['study_identifier']
                #try:
                invest = Investigation.objects.get(pk=accession)
                invest.delete()
                #except:
                #    connection._rollback()
            
            for r in raw_data_master_list:
                r.delete()
            for p in processed_data_master_list:
                p.delete()
            for pub in publications_master_list:
                pub.delete()
            """
     

    """
    Name: handle_label
    Description:
        main program; calls the parsing and insertion functions
    """   
    def handle_label(self, label, **options):
        """ main program """
        base_dir = settings.ISA_TAB_DIR

        isa_ref = label
        sys.stderr.write("%s\n" % label)
        
        isa_dir = os.path.join(base_dir, isa_ref)
        
        assert os.path.isdir(isa_dir), "Invalid Accession: %s" % isa_ref

        
        #assign files to proper file locations and make sure they're correct
        try:    
            investigation_file = glob.glob("%s/i_*.txt" % isa_dir).pop()
        except IndexError:
            raise "Missing investigation file\n"
        
        try:
            study_file = glob.glob("%s/s_*.txt" % isa_dir).pop()
        except IndexError:
            raise "Missing study file\n"

        try:
            assay_file = glob.glob("%s/a_*.txt" % isa_dir).pop()
        except IndexError:
            raise "Missing study file\n"
        
        isatab_file = os.path.join(isa_dir, "%s.zip" % isa_ref)
        #assert os.path.exists(isatab_file), "%s doesn't exist" % isatab_file
        
        try:
            pre_isatab_file = glob.glob("%s/*_%s.zip" % (isa_dir, isa_ref)).pop()
        except IndexError:
            pre_isatab_file = ''

        investigation_list = self.parse_investigation_file(investigation_file)
        
        
        #list that will hold dictionary(ies) of study file information
        study_list = list()
        #dictionary of lists of dictionaries; 
        assay_dictionary = defaultdict(list)
        
        
        for i_dict in investigation_list:
            #parse in the corresponding study file
            study_file_name = i_dict['tion'][0]['study_file_name']
            study_file = os.path.join(isa_dir, study_file_name)
            assert os.path.exists(study_file), "Study File Missing: %s" % study_file

            study_dict = self.parse_study_file(study_file)
            #print study_dict
            study_list.append(study_dict)
            
            #parse in corresponding assay file(s)
            inv_accession = i_dict['tion'][0]['study_identifier']
            study_assay_list = i_dict['sa']
            for sa in study_assay_list:
                assay_file_name = sa['study_assay_file_name']
                print assay_file_name
                assay_file = os.path.join(isa_dir, assay_file_name)
                assert os.path.exists(assay_file), "Assay File Missing: %s" % assay_file
                
                assay_dict = self.parse_assay_file(assay_file, inv_accession)
                #print assay_dict
                assay_dictionary[inv_accession].append(assay_dict)

        self.insert_isatab(investigation_list, isatab_file, pre_isatab_file, study_list, assay_dictionary)