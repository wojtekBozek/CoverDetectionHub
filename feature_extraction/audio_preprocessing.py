import librosa
import torchaudio
import torch
import numpy as np
from feature_extraction.feature_extraction import extract_features_cqt

import essentia.standard as estd

TARGET_SR = 22050
MAX_LEN = 100
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")



# Preprocess audio
def preprocess_audio(file_path, target_sr=TARGET_SR, max_len=MAX_LEN):
    waveform, sr = torchaudio.load(file_path)
    resample = torchaudio.transforms.Resample(orig_freq=sr, new_freq=target_sr)
    waveform = resample(waveform)

    if waveform.size(0) > 1:  # Convert to mono
        waveform = waveform.mean(dim=0, keepdim=True)

    max_samples = target_sr * max_len
    if waveform.size(1) > max_samples:
        waveform = waveform[:, :max_samples]
    else:
        pad = max_samples - waveform.size(1)
        waveform = torch.nn.functional.pad(waveform, (0, pad))

    return waveform.squeeze(0)  # Return 1D tensor

# Preprocess Audio
def preprocess_audio_coverhunter(file_path, target_sr=16000, max_len=100):
    """
    Preprocess an audio file for CoverHunter by resampling, padding, and extracting CSI features.

    Args:
        file_path (str): Path to the audio file.
        target_sr (int): Target sample rate for the audio file.
        max_len (int): Maximum length of the audio in seconds.

    Returns:
        torch.Tensor: CSI features with shape [1, frame_size, feat_size].
    """
    # Step 1: Load and Resample Audio
    waveform, sr = torchaudio.load(file_path)
    resample = torchaudio.transforms.Resample(orig_freq=sr, new_freq=target_sr)
    waveform = resample(waveform)

    # Convert to Mono
    if waveform.size(0) > 1:  # If stereo, average channels to create mono
        waveform = waveform.mean(dim=0, keepdim=True)

    # Trim or Pad Audio to max_len
    max_samples = target_sr * max_len
    if waveform.size(1) > max_samples:
        waveform = waveform[:, :max_samples]
    else:
        pad = max_samples - waveform.size(1)
        waveform = torch.nn.functional.pad(waveform, (0, pad))

    # Normalize Audio
    waveform_np = waveform.squeeze(0).numpy()  # Convert to NumPy for compatibility with PyCqt
    waveform_np = waveform_np / max(0.001, np.max(np.abs(waveform_np))) * 0.999

    # Extract CSI Features using PyCqt
    cqt = extract_features_cqt(audio_np=waveform_np,sample_rate=target_sr)

    # Add Batch Dimension
    cqt_tensor = torch.tensor(cqt, dtype=torch.float32).unsqueeze(0)  # Shape: [1, frame_size, feat_size]

    return cqt_tensor.to(DEVICE)


import ffmpeg
import os

class InvalidMediaFileError(Exception):
    """Exception raised when the input file is not a valid media file."""
    pass

def validate_audio(filepath):
    """
    Validates and preprocesses an audio file.

    Parameters:
        filepath (str): Path to the input file.

    Returns:
        tuple: (processed_filepath, error_message)
        - processed_filepath: Path to the processed audio file, or None if invalid.
        - error_message: Error message if the file is invalid, or None if valid.

    Raises:
        InvalidMediaFileError: If the file is not a valid media file.
    """
    # Probe the file using ffmpeg
    try:
        probe = ffmpeg.probe(filepath)
    except ffmpeg.Error:
        raise InvalidMediaFileError("The file is not a valid media file or cannot be processed.")

    format_info = probe.get("format", {})
    duration = float(format_info.get("duration", 0))
    size = float(format_info.get("size", 0)) / (1024 * 1024)  # Convert size to MB

    # Reject if file is too large or too long
    if size > 100:
        return None, "File is too large (over 100MB)."
    if duration > 20 * 60:  # 20 minutes
        return None, "File is too long (over 20 minutes)."

    # Check if it's already an audio file
    audio_streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if len(audio_streams) > 0:
        return filepath, None

    # If it's a video file, extract audio and convert to WAV with sr=16k
    output_filepath = os.path.splitext(filepath)[0] + ".wav"
    ffmpeg.input(filepath).output(output_filepath, format="wav", ac=1, ar="16000").run(overwrite_output=True)
    return output_filepath, None

# Audio preprocessing
def crema(audio_file, fs=44100, hop_length=512):
    """
    Compute "convolutional and recurrent estimators for music analysis" (CREMA)
    and resample so that it's reported in hop_length intervals
    NOTE: This code is a bit finnecky, and is recommended for Python 3.5.
    Check `wrapper_cream_feature` for the actual implementation.

    Returns
    -------
    crema: ndarray(n_frames, 12)
        The crema coefficients at each frame
    """
    from crema.models.chord import ChordModel
    from scipy import interpolate

    audio_vector = estd.MonoLoader(filename=audio_file, sampleRate=fs)()

    model = ChordModel()
    data = model.outputs(y=audio_vector, sr=fs)
    fac = (float(fs) / 44100.0) * 4096.0 / hop_length
    times_orig = fac * np.arange(len(data["chord_bass"]))
    nwins = int(np.floor(float(audio_vector.size) / hop_length))
    times_new = np.arange(nwins)
    interp = interpolate.interp1d(
        times_orig, data["chord_pitch"].T, kind="nearest", fill_value="extrapolate"
    )
    return interp(times_new).T

def process_crema(audio_path, output_dir="", output_file_pt=""):

    if os.path.exists(os.path.join(output_dir, output_file_pt)) == False:
        data_list = []
        labels_list = []
    else:
        test = torch.load(os.path.join(output_dir, output_file_pt))
        data_list = test["data"]
        labels_list = test["labels"]

    #out_dict = dict()
    #out_dict["crema"] = crema(audio_path)

    #label = audio_path.split("/")[-2]

    temp_crema = crema(audio_path)

    #os.makedirs(output_dir, exist_ok=True)

    idxs = np.arange(0, temp_crema.shape[0], 8)
    temp_tensor = torch.from_numpy(temp_crema[idxs].T)

    # expanding in the pitch dimension, and adding the feature tensor and its label to the respective lists
    return torch.cat((temp_tensor, temp_tensor))[:23].unsqueeze(0)
