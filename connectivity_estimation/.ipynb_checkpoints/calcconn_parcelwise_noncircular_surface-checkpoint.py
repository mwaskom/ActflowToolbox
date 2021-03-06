
import numpy as np
import nibabel as nib
import h5py
import os
import pkg_resources
from .multregconn import *
from .corrcoefconn import *
from .pc_multregconn import *

dilateMM = 10

partitiondir = pkg_resources.resource_filename('ActflowToolbox.dependencies', 'ColeAnticevicNetPartition/')
defaultdlabelfile = partitiondir + 'CortexSubcortex_ColeAnticevic_NetPartition_wSubcorGSR_parcels_LR.dlabel.nii'
dilatedmaskdir = pkg_resources.resource_filename('ActflowToolbox.network_definitions', 'Glasser2016/surfaceMasks/')

def calcconn_parcelwise_noncircular_surface(data, connmethod='multreg', dlabelfile=defaultdlabelfile, dilated_parcels=True, precomputedRegularTS=None, verbose=False):
    """
    This function produces a parcel-to-parcel connectivity matrix while excluding vertices in the neighborhood of a given target parcel.
    Excludes all vertices within a 10mm (default) dilated mask of the target parcel when computing parcel-to-parcel connectivity.
    Takes in vertex-wise data and generates a parcel X parcel connectivity matrix based on provided connmethod
    Currently only works for surface-based cortex connectivity
    
    PARAMETERS:
        data        : vertex-wise data... vertices x time; default assumes that data is 96k dense array
        connmethod  : a string indicating what connectivity method to use. Options: 'multreg' (default), 'pearsoncorr', 'pc_multregconn'
        dlabelfile  : parcellation file; each vertex indicates the number corresponding to each parcel. dlabelfile needs to match same vertex dimensions of data
        dilated_parcels :       If True, will exclude vertices within 10mm of a target parcel's borders when computing mult regression fc (reducing spatial autocorrelation inflation)
        precomputedRegularTS:  optional input of precomputed 'regular' mean time series with original region set. This might cut down on computation time if provided.
        verbose  :    indicate if additional print commands should be used to update user on progress
    RETURNS:
        fc_matrix       :       Target X Source FC Matrix. Sources-to-target mappings are organized as rows (targets) from each column (source)
    """

    nparcels = 360
    parcel_arr = np.arange(nparcels)
    # Load dlabel file (cifti)
    if verbose: print('Loading in CIFTI dlabel file')
    dlabels = np.squeeze(nib.load(dlabelfile).get_data())
    # Find and sort unique parcels
    unique_parcels = np.sort(np.unique(dlabels))
    # Only include cortex
    unique_parcels = unique_parcels[:nparcels]
                                            
    # Instantiate empty time series matrix for regular mean time series, or load from memory if provided
    if precomputedRegularTS is not None:
        regular_ts_matrix = precomputedRegularTS
        regular_ts_computed = np.ones((nparcels,1),dtype=bool)
    else:
        regular_ts_matrix = np.zeros((nparcels,data.shape[1]))
        regular_ts_computed = np.zeros((nparcels,1))
                                                 
    # Instantiate empty fc matrix
    fc_matrix = np.zeros((nparcels,nparcels))

    for parcel in unique_parcels:
        if verbose: print('Computing FC for target parcel', int(parcel))

        # Find where this parcel is in the unique parcel array
        parcel_ind = np.where(unique_parcels==parcel)[0]
        # Load in mask for target parcel
        if dilated_parcels:
            parcel_mask = np.squeeze(nib.load(dilatedmaskdir+'GlasserParcel' + str(int(parcel)) + '_dilated_10mm.dscalar.nii').get_data())
        else:
            parcel_mask = np.squeeze(nib.load(dilatedmaskdir+'GlasserParcel' + str(int(parcel)) + '.dscalar.nii').get_data())

        # get all target ROI indices
        target_ind = np.squeeze(nib.load(dilatedmaskdir+'GlasserParcel' + str(int(parcel)) + '.dscalar.nii').get_data())
        target_ind = np.asarray(target_ind,dtype=bool)

        # remove target parcel's mask from set of possible source vertices
        mask_ind = np.where(parcel_mask==1.0)[0] # find mask indices
        source_indices = dlabels.copy() # copy the original parcellation dlabel file
        source_indices[mask_ind] = 0 # modify original dlabel file to remove any vertices that are in the mask

        # Identify all 'source' parcels to include when computing FC
        source_parcels = np.delete(unique_parcels, parcel_ind)

        # Now compute mean time series of each ROI using modified dlabel file after removing target parcel's mask (ie source_indices)
        source_parcel_ts = np.zeros((len(source_parcels),data.shape[1])) # source regions X time matrix
        empty_source_row = [] # empty array to keep track of the row index of any sources that might be excluced
        i = 0
        for source in source_parcels:
            source_ind = np.where(source_indices==source)[0] # Find source parcel indices (from modified dlabel file)
            sourceInt = int(source)-1
            
            #Determine if this source parcel was modified (if not, then use standard time series)
            source_ind_orig = np.where(dlabels==source)[0]
            if np.array_equal(source_ind,source_ind_orig):
                
                if regular_ts_computed[sourceInt]:
                    source_parcel_ts[i,:] = regular_ts_matrix[sourceInt,:]
                else:
                    source_parcel_ts[i,:] = np.nanmean(np.real(data[source_ind,:]),axis=0) # compute averaged time series of source parcel
                    #Save time series for future use
                    regular_ts_matrix[sourceInt,:] = source_parcel_ts[i,:].copy()
                    regular_ts_computed[sourceInt] = True
            
            else:
                                                 
                # If the entire parcel is excluded (i.e, the time series is all 0s), then skip computing the mean for this parcel
                if len(source_ind)==0:
                    empty_source_row.append(i) # if this source is empty, remember its row (to delete it from the regressor matrix later)
                    i += 1
                    # Go to next source parcel
                    continue

                source_parcel_ts[i,:] = np.nanmean(np.real(data[source_ind,:]),axis=0) # compute averaged time series of source parcel
   
            i += 1

        # Delete source regions that have been entirely excluded from the source_parcels due to the dilation
        if len(empty_source_row)>0:
            source_parcel_ts = np.delete(source_parcel_ts,empty_source_row,axis=0) # delete all ROIs with all 0s from regressor matrix
            source_parcels = np.delete(source_parcels,empty_source_row,axis=0) # Delete the 0-variance ROI from the list of sources

        # compute averaged time series of TARGET
        parcelInt = int(parcel)-1
        if regular_ts_computed[parcelInt]:
            target_parcel_ts = regular_ts_matrix[parcelInt,:]
        else:
            target_parcel_ts = np.mean(np.real(data[target_ind,:]),axis=0)
            #Save time series for future use
            regular_ts_matrix[parcelInt,:] = target_parcel_ts.copy()
            regular_ts_computed[parcelInt] = True

        # Find matrix indices for all source parcels
        source_cols = np.asarray((source_parcels - 1),dtype=int) # subtract by 1 since source_parcels are organized from 1-360, and need to transform to python indices
        target_row = int(parcel - 1) # subtract by 1 to fit to python indices

        if connmethod == 'multreg':
            # run multiple regression, and add constant
            fc_matrix[target_row,source_cols] = multregconn(source_parcel_ts,target_parcel_ts)
        elif connmethod == 'pearsoncorr':
            fc_matrix[target_row,source_cols] = corrcoefconn(source_parcel_ts,target_parcel_ts)
        elif connmethod == 'pc_multregconn':
            fc_matrix[target_row,source_cols] = pc_multregconn(source_parcel_ts,target_parcel_ts)

    return fc_matrix
