#tbox_translational.py
#Run translational predictions on pre-extracted T-boxes
#Use tbox_translational_genomic.py instead for directly running on genomes

import hashlib
import base64
import sys
import re
import pandas as pd
from Bio import SeqIO
import subprocess

#Function to read an INFERNAL output file and extract sequence names, metadata, structure, and sequence
#Metadata is as described in the INFERNAL manual
def read_INFERNAL(file):
    metadata = []
    Tbox_start = -1
    Tbox_end = -1
    sp = ""
    sp_start = ""

    metadataLine = -1
    structLine = -1
    seqLine = -1
    
    #Don't parse strand (will all be +)
    Tboxes = {'Name':[], 'Rank':[], 'E_value':[], 'Score':[], 'Bias':[], 'Tbox_start':[],'Tbox_end':[],
               'CM_accuracy':[], 'GC':[], 'Sequence':[], 'Structure':[]}

    with open(file) as f:
        for lineCount, line in enumerate(f):
            if re.match(">>", line) is not None: #We found a match!
                Tboxes['Name'].append(line.split(" ")[1])

                metadataLine = lineCount + 3
                structLine = lineCount + 6
                seqLine = lineCount + 9

            if lineCount == metadataLine:
                metadata = list(filter(None, line.split(' '))) #Splits by spaces, and strips empty strings

                Tboxes['Rank'].append(int(metadata[0][1:len(metadata[0])-1])) #All except first and last characters
                Tboxes['E_value'].append(float(metadata[2])) #third entry
                Tboxes['Score'].append(float(metadata[3])) #fourth
                Tboxes['Bias'].append(float(metadata[4])) #fifth
                Tbox_start = metadata[9]
                Tboxes['Tbox_start'].append(int(Tbox_start))
                Tbox_end = metadata[10]
                Tboxes['Tbox_end'].append(int(Tbox_end))
                #Tboxes['Strand'].append(metadata[11])
                Tboxes['CM_accuracy'].append(float(metadata[13]))
                Tboxes['GC'].append(float(metadata[15][0:4])) #ignore the \n at the end

            if lineCount == structLine:
                sp = list(filter(None,line.split(' ')))
                structure = sp[0]
                Tboxes['Structure'].append(structure) #Take the second-to-last one

            if lineCount == seqLine:
                seq_line = list(filter(None, line.strip().split(' ')))
                sequence = ' '.join(seq_line[2:len(seq_line) - 1])

                #Fallback method (adapted from Thomas Jordan's code):
                if sequence not in line: #This can happen if there are two consecutive spaces in the middle of the sequence.
                    rsp = line.rsplit(Tbox_end, maxsplit = 1)
                    lsp = rsp[0].split(Tbox_start + ' ')
                    sequence = lsp[len(lsp) - 1].strip()
                    print("Fallback on line %d" % lineCount)

                #Do a sanity check
                if len(sequence) != len(structure): #This is an error!
                    print("\nParsing ERROR occured on line %d" % lineCount)
                    print(line) #For debug purposes
                    print(sequence)
                    print(structure)
                    print(seq_line)
                    #sequence = "ERROR"
                    
                Tboxes['Sequence'].append(sequence)
                
    return pd.DataFrame(Tboxes)

#Function to find features of a T-box given the secondary structure
#Parameters: 
#seq is the sequence containing the T-box
#struct is the secondary structure
#start is the position of the T-box within the structure (default = 1)

#Output:
#Stem 1 start, stem 1 specifier loop, codon, stem 1 end, antiterminator start, discriminator, antiterminator end

def tbox_features_translational(seq, struct, offset = 0):
    s1_start = -1
    s1_end = -1
    s1_loop_start = -1
    s1_loop_end = -1
    antiterm_start = -1
    antiterm_end = -1
    warnings = ""
    codon = ""
    codon_region = ""
    discrim = ""
    discrim_start = -1
    discrim_end = -1
    
    if len(seq)!=len(struct):
        raise RuntimeError("Sequence length (%d) is not equal to structure length (%d)" % (len(seq), len(struct)))
        
    if not (struct.startswith(':') or struct.startswith('<')):
        warnings += "BAD_SEC_STRUCT;"
    
    #Find the Stem 1 start
    m = re.search('<',struct)
    if m is not None:
        s1_start = m.start() #Gets the first '<'
    else:
        warnings += "NO_STEM1_START;"
        
    #Find the Stem 1 end
    m = re.search('>\.*,',struct)
    if m is not None:
        s1_end = m.start() #Gets the first '>,' or possibly '>.,'
    else:
        warnings += "NO_STEM1_END;"

    #Find the Stem 1 specifier loop start
    m = re.search('<\.*_',struct)
    if m is not None:
        s1_loop_start = m.start() #Gets the first '<_' or possibly '<._'
    else:
        warnings += "NO_SPEC_START;"
    
    #Find the Stem 1 specifier loop end
    m = re.search('_\.*>',struct)
    if m is not None and m.start() < s1_end:
        s1_loop_end = m.start() #Gets the first '_>' or possibly '_>'
    else:
        warnings += "NO_SPEC_END;"

    if s1_loop_end > s1_loop_start: #Sanity check
        #Read the codon
        codon = seq[s1_loop_end - 3: s1_loop_end] 
        #Check the codon
        if re.search('[^AaCcGgUu]', codon) is not None:
            warnings += "BAD_CODON;"
        else: #Assign the codon region
            minus_one_pos = s1_loop_end - 6
            while minus_one_pos > 0 and re.match('[^AaCcGgUu]', seq[minus_one_pos]) is not None:
                minus_one_pos -= 1 #Look for the first ACGU character before the codon
            plus_one_pos = s1_loop_end - 2
            while plus_one_pos < len(seq) - 1 and re.match('[^AaCcGgUu]', seq[plus_one_pos]) is not None:
                plus_one_pos += 1 #Look for the first ACGU character after the codon
            codon_region = seq[minus_one_pos] + codon + seq[plus_one_pos] #Get the surrounding region too, for +1/-1 predictions
    else:
        warnings += "NO_CODON;"
        
    #Find the antiterminator start
    antiterm_list = [m.start() for m in re.finditer(',\.*<', struct)] #Makes a list of all occurences of ',<'
    if len(antiterm_list) > 0:
        antiterm_start = antiterm_list[len(antiterm_list)-1] #Gets the last one
        discrim_start = struct.find('---', antiterm_start + 3) #Find the loop containing the discriminator
        discrim_end = discrim_start + 4
        discrim = seq[discrim_start:discrim_end]
    else:
        warnings += "NO_ANTITERM_START;"
    
    #Check the discriminator
    if not discrim.startswith('UGG') or re.search('[^AaCcGgUu]', discrim) is not None:
        warnings += "BAD_DISCRIM;"
    
    #Find the antiterminator
    match = re.search('>\.*:',struct)
    if match is not None: #Sometimes the antiterminator end is missing from the sequence
        antiterm_end = match.start()
    else: #Simply get the last '>'
        end_list = [m.start() for m in re.finditer('>', struct)]
        if len(end_list) > 0:
            antiterm_end = end_list[len(end_list)-1]
        else:
            warnings += "NO_ANTITERM_END;"
    
    #Adjust values based on offset
    s1_start += offset
    s1_loop_start += offset + 1
    s1_loop_end += offset
    s1_end += offset
    antiterm_start += offset + 1
    antiterm_end += offset
    discrim_start += offset
    discrim_end += offset - 1
    
    #Return a tuple with the features identified
    return (s1_start, s1_loop_start, s1_loop_end, codon, s1_end, antiterm_start, discrim, antiterm_end, codon_region, warnings, discrim_start, discrim_end)
    
#Convert between position in INFERNAL output and fasta sequence
#Count the gaps and adjust for them
#Returns a mapping between INFERNAL position and fasta position
def map_fasta(seq, fasta, offset = 0, allowed = 'AaGgCcUu'):
    #Initialize counters
    count_fasta = offset
    parse_buffer = ""
    parsing = False
    mapping = []

    for c in seq:
        mapping.append(count_fasta)
        
        if parsing:
            if c == ']': #The end of the numerical gap
                parsing = False
                count_fasta += int(parse_buffer.strip()) #Parse the value
                #print(parse_buffer) #debug
                parse_buffer = "" #reset buffer for the next gap
            else:
                parse_buffer += c #Add the character to the parse buffer
            
        elif c in allowed:
            count_fasta += 1
            
        elif c == '[': # The start of a numerical gap
            parsing = True
        
    return mapping

#Function to compute derived T-box features from the prediction
#Note: tboxes must contain fasta sequences!
def tbox_derive(tboxes):
    #Derive more features for visualization

    for i, fasta in enumerate(tboxes['FASTA_sequence']):
        print('Mapping ' + tboxes['Name'][i]) #debug
        seq = tboxes['Sequence'][i]
        if isinstance(seq, str): #sanity check. Skip NaN
            #Create mapping between INFERNAL sequence and FASTA sequence
            mapping = map_fasta(seq, fasta, offset = tboxes['Tbox_start'][i] - 1)
            #Update the positions of existing features.
            s1_start = int(tboxes['s1_start'][i])
            if s1_start > 0:
                tboxes.at[tboxes.index[i], 's1_start'] = mapping[s1_start]

            s1_loop_start = int(tboxes['s1_loop_start'][i])
            if s1_loop_start > 0:
                tboxes.at[tboxes.index[i], 's1_loop_start'] = mapping[s1_loop_start]

            s1_loop_end = int(tboxes['s1_loop_end'][i])
            if s1_loop_end > 0:
                if s1_loop_end < len(mapping):
                    tboxes.at[tboxes.index[i], 's1_loop_end'] = mapping[s1_loop_end]
                    #Calculate codon range
                    tboxes.at[tboxes.index[i], 'codon_start'] = mapping[s1_loop_end - 3]
                    tboxes.at[tboxes.index[i], 'codon_end'] = mapping[s1_loop_end - 1]
                else:
                    print("Warning: mapping error for s1_loop_end:")
                    print(s1_loop_end)
                    print(len(mapping))
                    print(mapping)

            s1_end = int(tboxes['s1_end'][i])
            if s1_end > 0:
                tboxes.at[tboxes.index[i], 's1_end'] = mapping[s1_end]

            aterm_start = int(tboxes['antiterm_start'][i])
            if aterm_start > 0:
                tboxes.at[tboxes.index[i], 'antiterm_start'] = mapping[aterm_start]
                
            #Calculate discriminator range
            discrim_start = int(tboxes['discrim_start'][i])
            if discrim_start > 0:
                tboxes.at[tboxes.index[i], 'discrim_start'] = mapping[discrim_start]
                tboxes.at[tboxes.index[i], 'discrim_end'] = mapping[discrim_start +  3] #+3 because inclusive

            aterm_end = int(tboxes['antiterm_end'][i])
            if aterm_end > 0:
                aterm_end = min(aterm_end, len(mapping)-1)
                tboxes.at[tboxes.index[i], 'antiterm_end'] = mapping[aterm_end]
                #Calculate terminator end
                #tboxes.at[tboxes.index[i], 'term_end'] = term_end(fasta, aterm_end)
        
    return tboxes

#RNAfold on target sequence
def get_fold(sequence):
    #Initialize outputs
    structure = ""
    energy = ""
    errors = ""

    vienna_args = ['RNAfold', '--noPS', '-T', '37'] # arguments used to call RNAfold at 37 degrees
    vienna_input = str(sequence) # the input format
    vienna_call = subprocess.run(vienna_args, stdout = subprocess.PIPE, stderr = subprocess.PIPE, input = vienna_input, encoding = 'ascii')
    
    output = vienna_call.stdout.split('\n')
    if len(output) > 1: # if there is a result
        output = output[-2]
        output = output.split()
        structure = output[0] # gets last line's structure (always will be first element when sliced)
        energy = output[-1].replace(')', '').replace('(', '') # get energy (always will be last element in slice)
    errors = vienna_call.stderr.replace('\n',' ')
    return structure, energy, errors

#RNAfold on target sequence, with constraints
def get_fold_constraints(sequence, structure):
    #Initialize outputs
    energy = ""
    errors = ""
    structure_out = ""

    vienna_args = ['RNAfold', '--noPS', '-T', '37', '-C'] # arguments used to call RNAfold at 37 degrees with constraints
    vienna_input = str(sequence) + '\n' + str(structure) # the input format
    vienna_call = subprocess.run(vienna_args, stdout = subprocess.PIPE, stderr = subprocess.PIPE, input = vienna_input, encoding = 'ascii')
    
    output = vienna_call.stdout.split('\n')
    if len(output) > 1: # if there is a result
        output = output[-2]
        output = output.split()
        structure_out = output[0] # gets last line's structure (always will be first element when sliced)
        energy = output[-1].replace(')', '').replace('(', '') # get energy (always will be last element in slice)
    errors = vienna_call.stderr.replace('\n','') #changed from ' '
    return structure_out, energy, errors

#Make antiterminator constraints for folding
def make_antiterm_constraints(sequence, structure):
    if pd.isna(sequence) or pd.isna(structure):
        return None, None, None
    
    # search for antiterm end
    antiterm_end = term_end(structure, 0, pattern = ')') + 1 #search for last ')', which is the antiterm end

    # search for bulge of 4 or more bases
    match = re.search('[(][\.]{4,}[(]', structure)
    if(match):
        #Find TGGN
        discriminator = re.search('[T][G][G][ATGC]', sequence[(match.start() + 1):match.end()])
        # make hard constraint for unpaired 3' from antiterm end
        constraints = structure[:antiterm_end] + 'x' * len(structure[antiterm_end:])
        # make hard constraint for discriminator 
        if(discriminator):
            constraints = constraints[:(discriminator.start() + match.start() + 1)] + 'xxxx' + constraints[(discriminator.end() + match.start() + 1):]
            structure_out, energy, errors = get_fold_constraints(sequence, constraints)
            if len(structure_out) < 4 or structure_out[3] != '(': #check if base immediately before UGGN is paired
                errors += "BAD_ANTITERM_STEM"
            return structure_out, energy, errors
    return None, None, None

#RNAeval to get energy
def get_energy(sequence, structure):
    if pd.isna(sequence) or pd.isna(structure):
        return None, ""

    vienna_args = ['RNAeval', '-T', '37'] # arguments that are used to call vienna RNAeval at T 37 degrees  
    vienna_input = str(sequence + "\n" + structure) # the input format
    vienna_call = subprocess.run(vienna_args, stdout = subprocess.PIPE, stderr = subprocess.PIPE, input = vienna_input, encoding = 'ascii')
    # calls the subprocess with the vienna_input as input for the program
    energy = vienna_call.stdout.split()
    if energy: # if there actually is a result
        energy = energy[-1].replace('(', '').replace(')', '')
    errors = vienna_call.stderr.replace('\n',' ')
    return energy, errors

def get_sequence(fasta, start, end):
    if pd.isna(fasta) or pd.isna(start) or pd.isna(end):
        return None
    start, end = int(start), int(end)
    sequence = fasta[start:end]
    return sequence

def get_whole_structs(antiterm_start, whole_antiterm_structure, terminator_structure):
    if pd.isna(antiterm_start) or pd.isna(whole_antiterm_structure) or pd.isna(terminator_structure):
        return None, None
    start = int(antiterm_start) - 1 #NOTE: this is 1-indexed! if it's 0 indexed it will be off by one
    if start > 0:
        whole_term = whole_antiterm_structure[:start] + terminator_structure
        term_start = whole_term.find('(',start) + 1 #convert to 1-indexed
        return whole_term, term_start
    return None, None

def transcribe(sequence):
    # transcribes DNA to RNA 
    tab = str.maketrans("ACGTacgt", "ACGUacgu")
    return sequence.translate(tab)

def replace_characters(structure):
    # replaces the weird infernal characters with normal dot brackets
    chars = ":,.~_-" # all characters representing unpaired structs, INCLUDING truncations (assumption)
    for c in chars:
        if c in structure:
            structure = structure.replace(c, ".") # creates a normal dot instead
    chars = "<{[(" # all characters representing paired bases
    for c in chars:
        if c in structure:
            structure = structure.replace(c, "(") # creates a normal open bracket
    chars = ">}])" # all characters representing paired bases
    for c in chars:
        if c in structure:
            structure = structure.replace(c, ")") # creates a normal closed bracket
    return structure

# removes truncations and adds the missing nucleotides from the FASTA
def resolve_truncations(fasta_sequence, processed_sequence, processed_structure):

    processed_length = len(processed_sequence) 
    
    for truncations in re.finditer('\*\[.+?\][\*>]', processed_sequence):
        # finds the characters representing the truncation in the sequence
        # truncations have the form *[ N]* where N is a number of nucleotides
        # regexp : finds *[ followed by optional space, then number, then ]* or ]> if end of sequence
        #print(truncations.group(),truncations.start())
        
        difference = len(processed_sequence) - processed_length 
        # as we are modifying the length of the processed_sequence inside the for loop, we need to adjust
        # our indices obtained from the regexp by how much the sequence length has changed 
        
        missing = int(truncations.group().replace('*', '').replace('[', '').replace(']','').replace(' ', ''))
        # obtain the number of missing nucleotides by stripping away the useless characters
        
        match_start, match_end = truncations.start() + difference, truncations.end() + difference
        
        sequence_gaps = processed_sequence[:match_start].count('-')
        # number of gaps from start of the sequence to the truncation itself, important for alignment
        
        # obtain the positions of the truncations (adjusted by the difference in length
        # adds the missing n nucleotides to the sequence by getting them from the fasta sequence
        
        if(missing > 0):
            processed_sequence = processed_sequence[:match_start] + transcribe(fasta_sequence[(match_start - sequence_gaps):(match_start - sequence_gaps + missing)]) + processed_sequence[match_end:]
            processed_structure = processed_structure[:match_start] + "~" * (missing) + processed_structure[match_end:]
        else:
            processed_sequence = processed_sequence[:match_start] + processed_sequence[match_end:]
            processed_structure = processed_structure[:match_start] + processed_structure[match_end:]
    # appends the start of the T-box fasta if it is not in the infernal sequence
    return processed_sequence, processed_structure


#Makes a list of (lists of length 4) containing pair information
# in pairs, the first element of each entry is the character type and the second its index
# the third is the index of the bracket it is matched with and the fourth the matching bracket
def make_pairs(processed_structure, dot_structure):
    struct_list = list(dot_structure)
    length_list = len(struct_list) # length of the list struct_list
    pairs = [None] * length_list # creates a list of length dot structure filled with None
    
    for index, element in enumerate(struct_list):
        if(element == '('):
            pairs[index] = ['(', index, None, ''] # creates entries for the left brackets in pairs
        elif(element == ')'):
            pairs[index] = [')', index, None, ''] # creates entries for the right brackets in pairs

    for index, element in enumerate(pairs):
        l_count = 0
        r_count = 0
        i = index + 1
        if(element != None):
            if(element[0] == '('):
                l_count += 1
                while(i < length_list):
                    if(pairs[i] != None):
                        if('(' == pairs[i][0]):
                            l_count += 1
                        elif(')' == pairs[i][0]):
                            r_count += 1
                            l_count -= 1
                        if((r_count > 0) and (l_count == 0)):
                            element[2] = pairs[i][1]
                            element[3] = processed_structure[pairs[i][1]]
                            pairs[i][2] = element[1]
                            pairs[i][3] = processed_structure[element[1]]
                            break
                    i += 1
    return pairs

def replace_gaps(processed_sequence, processed_structure, dot_structure, pairs):
    structure_changed = False # check whether structure was modified by program
    corrected_brackets = False # check whether brackets were adjusted
    
    my_dot_structure = dot_structure # creates a copy of dot_structure that does not get changed
    my_processed_sequence = processed_sequence # creates a copy of processed_sequence that does not get changed
    
    for match in re.finditer('-', my_processed_sequence):
        index = match.start() # gets the index of each gap '-' in the sequence
        
        if(my_dot_structure[index] == '.'):
            processed_structure = processed_structure[:index] + 'x' + processed_structure[(index + 1):]
            dot_structure = dot_structure[:index] + 'x' + dot_structure[(index + 1):]
            # replaces the secondary structure with an x to tag it for deletion
            processed_sequence = processed_sequence[:index] + 'x' + processed_sequence[(index + 1):]
            # replaces the nucleotide in the sequence with an x, tagging it for deletion
        
        elif((my_dot_structure[index] == '(') or (my_dot_structure[index] == ')')):
            processed_structure = processed_structure[:index] + 'x' + processed_structure[(index + 1):]
            dot_structure = dot_structure[:index] + 'x' + dot_structure[(index + 1):]
            # replaces the open/closed bracket in the secondary structures by x to tag it for deletion
            processed_sequence = processed_sequence[:index] + 'x' + processed_sequence[(index + 1):]
            # replaces the corresponding nucleotide in the sequence by x to tag it for deletion
            
            structure_changed = True # we have changed the sequence
            
            if(isinstance(pairs[index][2], int) == True): # if there is a matching bracket
                paired = pairs[index][2] # the position of the paired closed/open bracket
                if(my_processed_sequence[paired] != '-'):
                # if the matching closed/open bracket is a real nucleotide in the sequence and not '-'
                    processed_structure = processed_structure[:paired] + '.' + processed_structure[(paired + 1):]
                    dot_structure = dot_structure[:paired] + '.' + dot_structure[(paired + 1):]
                    # replace the matching closed/open bracket by a '.'
                    corrected_brackets = True
    return processed_sequence, processed_structure, dot_structure, structure_changed, corrected_brackets

def parse_structures(fasta_sequence, tbox_start, tbox_end, sequence, structure): 
    # Fills truncations, strips gaps, and aligns secondary structure
    # tbox_start and tbox_end are the positions of the tbox within the FASTA
    
    #Catch Nones and NaNs
    if any(pd.isna(arg) for arg in [fasta_sequence, tbox_start, tbox_end, sequence, structure]):
        return None, None, None
    
    tbox_start = int(tbox_start) # for some reason needed cuz its a float otherwise
    tbox_end = int(tbox_end)
    messages = '' # a string containing all messages that are not errors
    warnings = '' # a string containing all of the error messages
    processed_sequence = sequence # initializes the sequence for processing
    processed_structure = structure # initializes the structure for processing
    
    # count whether there is an unequal number of open and closed brackets
    count_open = structure.count('<')
    count_closed = structure.count('>')
    if(count_open != count_closed):
        warnings += ' unequal_number_of_brackets_in_input'
    
    #appends the start of the T-box fasta if it is not in the infernal sequence
    if(tbox_start > 1): 
        processed_sequence = transcribe(fasta_sequence[:(tbox_start -1)]) + processed_sequence
        processed_structure = "~" * (tbox_start - 1) + processed_structure
        
    # appends the end of the T-box fasta if it is not in the infernal sequence
    if tbox_end < len(fasta_sequence): 
        processed_sequence = processed_sequence + transcribe(fasta_sequence[tbox_end:])
        processed_structure = processed_structure + "~" * (len(fasta_sequence) - tbox_end)
    

    # Resolve internal truncations by filling with corresponding nucleotides from FASTA
    processed_sequence, processed_structure = resolve_truncations(fasta_sequence, processed_sequence, processed_structure)
    if(len(processed_sequence) != len(processed_structure)):
        warnings += ' unequal_lengths_structure_and_sequence'
        
    processed_sequence = processed_sequence.upper()
    dot_structure = replace_characters(processed_structure) # make a normal dot bracket structure
    
    pairs = make_pairs(processed_structure, dot_structure) # creates a list with all paired 
    
    processed_sequence, processed_structure, dot_structure, structure_changed, corrected_brackets = replace_gaps(processed_sequence, processed_structure, dot_structure, pairs)
    if(structure_changed == True):
        messages += ' bracket_structure_was_changed'
    if(corrected_brackets == True):
        messages += ' changed_some_brackets_to_dots'
    
    #Delete gaps
    processed_sequence = processed_sequence.replace('x', '')
    processed_structure = processed_structure.replace('x', '')
    dot_structure = dot_structure.replace('x', '')
    
    if(transcribe(fasta_sequence) != processed_sequence):
        warnings += ' fasta_and_sequence_do_not_match'
    
    #Fix loops
    dot_structure = dot_structure.replace('(..)', '....')
    dot_structure = dot_structure.replace('(.)', '...')
    dot_structure = dot_structure.replace('(())', '....')
    
    # count whether there is an unequal number of open and closed brackets
    count_open = dot_structure.count('(')
    count_closed = dot_structure.count(')')
    if(count_open != count_closed):
        warnings += ' unequal_number_of_brackets'

    # make pairs again to process the secondary structure features of the Tbox
    # Find stems
    my_pairs = make_pairs(processed_structure, dot_structure)
    index = 0
    structures = []
    while(index < len(dot_structure) - 1):
        if(my_pairs[index] != None):
            if(my_pairs[index][0] == '('):
                # this is a secondary structure element (a stem)
                #use 1-index
                structures.append([ my_pairs[index][1] + 1, my_pairs[index][2] + 1])
                index = my_pairs[index][2] # go to the paired bracket
        index += 1
    
    #Stem 3 is the second-to-last. Only calculate if there also a stem 1 (total stems >2)
    stem_three = None    
    if(len(structures) > 2):
        stem_three = structures[-1]
    
    #Return: dot structure, list of secondary structural elements, messages and warnings
    return dot_structure, structures, (messages + warnings)

def local_fold(sequence, length):
    vienna_args = ['RNALfold', '-T', '37', '-L', str(length), '−−noClosingGU'] # https://academic.oup.com/nar/article/40/12/5215/2414626
    # 100bp length will be good for our purpose
    vienna_input = str(sequence)
    vienna_call = subprocess.run(vienna_args, stdout = subprocess.PIPE, stderr = subprocess.PIPE, input = vienna_input, encoding = 'ascii')
    
    output = vienna_call.stdout.strip().split('\n')
    errors = vienna_call.stderr

    return output, errors

def term_end(sequence, start, pattern = 'TTTTT'):
    match = sequence.rfind(pattern, start) #get the last occurence of the pattern, after the start
    if match > 0:
        return match + len(pattern) #- 1
    return len(sequence) #fallback: return the end

def run_thermo(tboxes):
    #Don't need: fasta_antiterm_start, fasta_antiterm_end
    #From RUN:
    tboxes[['whole_antiterm_structure', 'other_stems', 'whole_antiterm_warnings']] = tboxes.apply(lambda x: parse_structures(x['FASTA_sequence'], x['Tbox_start'], x['Tbox_end'], x['Sequence'], x['Structure']), axis = 'columns', result_type = 'expand')
    print('Structure correction complete.')

    #Get the terminator sequence as a string
    tboxes['term_sequence'] = tboxes.apply(lambda x: get_sequence(x['FASTA_sequence'], x['discrim_end'], x['term_end']), axis = 'columns', result_type = 'expand')
    print('Terminators found.')
    # fold the terminator sequence obtained above to get the secondary structure
    tboxes[['term_structure', 'terminator_energy', 'term_errors']] = tboxes.apply(lambda x: get_fold(x['term_sequence']), axis = 'columns', result_type = 'expand')
    print('Terminators folded.')

    # get the sequence from antiterm start to term end, will be used for comparing conformations with equal length
    tboxes['antiterm_term_sequence'] = tboxes.apply(lambda x: get_sequence(x['FASTA_sequence'],x['antiterm_start'] - 1, x['term_end']), axis = 'columns', result_type = 'expand')
    print('Antiterminator sequence found.')
    # get the antiterminator structure
    tboxes['infernal_antiterminator_structure'] = tboxes.apply(lambda x: get_sequence(x['whole_antiterm_structure'],x['antiterm_start'] - 1, x['term_end']), axis = 'columns', result_type = 'expand')
    print('Antiterminator INFERNAL structure found.')
    # get energy using RNAeval
    # this is DEPRECATED because we'll do constrained folding refinement
    #tboxes[['infernal_antiterminator_energy', 'infernal_antiterminator_errors']] = tboxes.apply(lambda x: get_energy(x['antiterm_term_sequence'], x['infernal_antiterminator_structure']), axis = 'columns', result_type = 'expand')
    print('Antiterminator energy done.')
    # refold the antiterminator using RNAfold with hard constraints
    tboxes[['vienna_antiterminator_structure', 'vienna_antiterminator_energy', 'vienna_antiterminator_errors']] = tboxes.apply(lambda x: make_antiterm_constraints(x['antiterm_term_sequence'], x['infernal_antiterminator_structure']), axis = 'columns', result_type = 'expand')
    print('Antiterminator re-folding done.')
    # get the terminator structure and energy, structure is term structure with dots in front so that structure 
    # spans from antiterm_start to term_end. Use offset of 10 (instead of 8 for transcriptional T-boxes)
    tboxes['terminator_structure'] = tboxes.apply(lambda x: ('.' * 10 + x['term_structure']), axis = 'columns', result_type = 'expand')
    print('Terminator structure generated.')
    tboxes[['terminator_energy', 'terminator_errors']] = tboxes.apply(lambda x: get_energy(x['antiterm_term_sequence'], x['terminator_structure']), axis = 'columns', result_type = 'expand')
    print('Terminator energy calculated.')
    
    #PLACEHOLDER in place of terminator refinement
    tboxes['new_term_structure'] = ""
    tboxes['new_term_energy'] = None
    tboxes['new_term_errors'] = ""
    
    # make the tbox terminator structure. Use the refined structure.
    tboxes[['whole_term_structure', 'term_start']] = tboxes.apply(lambda x: get_whole_structs(x['antiterm_start'], x['whole_antiterm_structure'], x['terminator_structure']), axis = 'columns', result_type = 'expand')
    #make the Vienna antiterm structure for the whole T-box
    tboxes['folded_antiterm_structure'] = tboxes.apply(lambda x: get_whole_structs(x['antiterm_start'], x['whole_antiterm_structure'], x['vienna_antiterminator_structure'])[0], axis = 'columns', result_type = 'expand')
    print('Thermodynamic calculations complete.')
    return tboxes
    
#Generate trimmed structures and sequences
def trim(seq_df):
    seq_df['Trimmed_sequence']=""
    seq_df['Trimmed_antiterm_struct']=""
    seq_df['Trimmed_term_struct']=""

    counter = 0

    for i in range(0,len(seq_df)):
        print('Trimming ' + seq_df['Name'][i])
        seq = seq_df['FASTA_sequence'][i]
        t_struct = seq_df['whole_term_structure'][i]
        a_struct = seq_df['folded_antiterm_structure'][i]
    
        if pd.isna(seq) or pd.isna(t_struct) or pd.isna(a_struct) or pd.isna(seq_df['antiterm_end'][i]):
            continue
        
        tbox_start = int(seq_df['Tbox_start'][i])
        term_end = int(seq_df['term_end'][i]) #min(term_end_regex(seq,int(seq_df['antiterm_end'][i]) + 10),int(seq_df['term_end'][i]))
        seq_df.at[seq_df.index[i], 'term_end'] = term_end #update term_end
    
        if (tbox_start < -1) or term_end > len(seq): #note these are 1-indexed
            print('Error')
            continue
    
        #Slice fasta sequence to get the tbox sequence
        seq = seq[tbox_start-1:term_end]
    
        if len(seq) != len(a_struct):
            a_struct += '.'*(len(seq)-len(a_struct))
        
        if len(seq) != len(t_struct):
            t_struct += '.'*(len(seq)-len(t_struct))
    
        #Also slice the structures
        t_struct = t_struct[tbox_start-1:term_end]
        a_struct = a_struct[tbox_start-1:term_end]
    
        seq_df.at[seq_df.index[i], 'Trimmed_sequence'] = seq
        seq_df.at[seq_df.index[i], 'Trimmed_antiterm_struct'] = a_struct
        seq_df.at[seq_df.index[i], 'Trimmed_term_struct'] = t_struct
        counter += 1

    print("Trimmed sequences: " + str(counter))
    return seq_df

#The main function to predict T-boxes
def tbox_predict(INFERNAL_file, predictions_file, fasta_file = None, score_cutoff = 15):
    score_cutoff = int(score_cutoff) #makes it an int, if it was passed as a string

    #Read the input file into a dataframe
    tbox_all_DF = read_INFERNAL(INFERNAL_file)
    #Initialize the dataframe columns for prediction output
    tbox_all_DF['s1_start'] = -1
    tbox_all_DF['s1_loop_start'] = -1
    tbox_all_DF['s1_loop_end'] = -1
    tbox_all_DF['s1_end'] = -1
    tbox_all_DF['antiterm_start'] = -1
    tbox_all_DF['antiterm_end'] = -1
    tbox_all_DF['term_start'] = -1
    tbox_all_DF['term_end'] = -1
    tbox_all_DF['codon_start'] = -1
    tbox_all_DF['codon_end'] = -1
    tbox_all_DF['codon'] = ""
    tbox_all_DF['codon_region'] = ""
    tbox_all_DF['discrim_start'] = -1
    tbox_all_DF['discrim_end'] = -1
    tbox_all_DF['discriminator'] = ""
    tbox_all_DF['warnings'] = ""
    #Hash string for database
    #tbox_all_DF['hash_string'] = ""
    tbox_all_DF['type'] = "Translational"
    tbox_all_DF['source'] = fasta_file
    #tbox_all_DF['FASTA_sequence'] = ""
    
    downstream_bases = 0
        
    #Predict the t-boxes
    for i, name in enumerate(tbox_all_DF['Name']):
        print(name) #Write to log
        
        #Predict the features. Use offset of 1 to convert 0-indexed Python format to standard sequence format
        tbox = tbox_features_translational(tbox_all_DF['Sequence'][i], tbox_all_DF['Structure'][i], offset = 1)
        
        #Assign output to dataframe
        tbox_all_DF.at[tbox_all_DF.index[i], 's1_start'] = tbox[0]
        tbox_all_DF.at[tbox_all_DF.index[i], 's1_loop_start'] = tbox[1]
        tbox_all_DF.at[tbox_all_DF.index[i], 's1_loop_end'] = tbox[2]
        tbox_all_DF.at[tbox_all_DF.index[i], 'codon'] = tbox[3]
        tbox_all_DF.at[tbox_all_DF.index[i], 's1_end'] = tbox[4]
        tbox_all_DF.at[tbox_all_DF.index[i], 'antiterm_start'] = tbox[5]
        tbox_all_DF.at[tbox_all_DF.index[i], 'discriminator'] = tbox[6]
        tbox_all_DF.at[tbox_all_DF.index[i], 'antiterm_end'] = tbox[7]
        tbox_all_DF.at[tbox_all_DF.index[i], 'codon_region'] = tbox[8]
        tbox_all_DF.at[tbox_all_DF.index[i], 'warnings'] += tbox[9]
        tbox_all_DF.at[tbox_all_DF.index[i], 'discrim_start'] = tbox[10]
        tbox_all_DF.at[tbox_all_DF.index[i], 'discrim_end'] += tbox[11]

        #Make the hash string
        #hasher = hashlib.sha256(tbox_all_DF['Sequence'][i].encode('utf-8'))
        #hash =  str(base64.urlsafe_b64encode(hasher.digest()))
        #tbox_all_DF.at[tbox_all_DF.index[i],'hash_string'] = hash[2:12]
        
        #Check the score
        if tbox_all_DF.at[tbox_all_DF.index[i], 'Score'] < score_cutoff:
            tbox_all_DF.at[tbox_all_DF.index[i], 'warnings'] += "LOW_SCORE;"
    
    #Deduplicate
    tbox_all_DF.drop_duplicates(subset = 'Sequence', keep = 'first', inplace = True) #Drop duplicate T-boxes
    tbox_all_DF.reset_index(drop=True, inplace = True)
    
    #Write output (debug)
    tbox_all_DF.to_csv(predictions_file, index = False, header = True)
    
    print("Adding FASTA sequence data")
    
    #Get FASTA sequences from the master file
    #Perform the fasta processing (if enabled)
    if fasta_file is not None:
        fastas = {'Name':[], 'FASTA_sequence':[]}
        #Read the fasta file
        with open(fasta_file) as f:
            for fasta in SeqIO.parse(f,'fasta'):
                name, sequence = fasta.id, str(fasta.seq)
                fastas['Name'].append(name)
                fastas['FASTA_sequence'].append(sequence)
        #Merge with T-box dataframe
        fasta_DF = pd.DataFrame(fastas)
        #fasta_DF.to_csv('test_fasta.csv', index = True, header = True)
        tbox_all_DF = pd.merge(fasta_DF, tbox_all_DF, on = 'Name', how = 'left') #Left merge to preserve all FASTA sequences
    
    #Set the terminator end
    tbox_all_DF['term_end'] = tbox_all_DF['Tbox_end']
    
    #Calculate derived features and remap locations relative to fasta
    derived = tbox_derive(tbox_all_DF)
    #Checkpoint
    derived.to_csv(predictions_file, index = False, header = True)
    print("Starting thermo calculations")
    thermo = run_thermo(derived)
    
    #Trim to terminator end (placeholder function, does nothing for now)
    thermo = trim(thermo)
    
    #Write output
    thermo.to_csv(predictions_file, index = False, header = True)
    return 0

#Get arguments from command line and run the prediction
if len(sys.argv) > 5 or len(sys.argv) < 3:
    print("Error: incorrect number of arguments: %d" % len(sys.argv))
    print(sys.argv)
else:
    tbox_predict(*sys.argv[1:len(sys.argv)])
