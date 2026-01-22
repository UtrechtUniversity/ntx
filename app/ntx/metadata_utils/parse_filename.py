"""
Module for parsing MEA filenames and extracting structured metadata fields.

Supports irregular field ordering and identifies missing values.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)

COMPOUNDS = {'bpa', 'pfhxs', 'fluetizolam', 'flunitrazolam', 'lorazepam',
              'oxazepam', 'snakevenoms', 'microplastics', 'dieldrin', 'lindane',
              'ddt','dde'}
EXPERIMENT_TYPES = {"MEA"}
CELL_TYPES = {"rcortex"}
EXPOSURE_TYPES = {'acute', 'chronic', 'subchronic'}
BASELINE_EXPOSURE = {'baseline', 'exposure'}



def extract_mea_filename_metadata(
        filepath: Union[str, Path],
        check_missing: bool = False
) -> Dict[str, Optional[str]]:
    """
    Extract metadata from a MEA filename.

    Parameters
    ----------
    filename : Union[str, Path]
        path to the .csv file to parse.
    check_missing : bool
        If True, log a debug message for any missing metadata fields.

    Returns
    -------
    Dict[str, Optional[str]]
        A dictionary with extracted metadata fields.
    """
    path = Path(filepath)
    filename = path.name.replace(" ", "")
    tokens = filename.split('_')

    metadata = _initialize_metadata()
    extra_tokens = _parse_tokens(tokens, metadata)
    metadata['mea:extra_tokens'] = '_'.join(extra_tokens) if extra_tokens else None

    if check_missing:
        issues = _check_missing_fields(metadata, filename)
        if issues:
            logger.debug(issues)

    return metadata


def _initialize_metadata() -> Dict[str, Optional[str]]:
    return {
        'mea:date': None,
        'mea:experimenter': None,
        'mea:experiment_number': None,
        'mea:plate_number': None,
        'mea:type_of_cells': None,
        'mea:type_of_experiment': None,
        'mea:type_of_exposure': None,
        'mea:compound': None,
        'mea:baseline_exposure': None,
        'mea:sex': None,
        'mea:div': None,
        'mea:exposure_duration': None,
        'mea:extra_tokens': None
    }

def _parse_tokens(tokens: List[str], metadata: Dict[str, Optional[str]]) -> List[str]:  # noqa: PLR0912
    extra_tokens = []

    date_patterns = [r'\d{6}', r'\d{8}', r'\d{4}-\d{2}-\d{2}', r'\d{2}-[A-Za-z]{3}-\d{4}']
    initials_pattern = r'^[A-Za-z]{2,4}$'
    experiment_number_pattern = r'^\d{6}$'
    plate_number_pattern = r'^\d+-\d+$'
    div_pattern = r'DIV\d+(\(\d+\))?'
    duration_pattern = r'(\d+)\s*(s|sec|seconds|m|min|minutes|h|hr|hours|d|day|days)\b'

    for t in tokens:
        token = t.strip()
        if metadata['mea:date'] is None and any(re.fullmatch(p, token) for p in date_patterns):
            metadata['mea:date'] = token
        elif metadata['mea:experimenter'] is None and re.fullmatch(initials_pattern, token):
            metadata['mea:experimenter'] = token
        elif (
            metadata['mea:experiment_number'] is None
            and re.fullmatch(experiment_number_pattern, token)
        ):
            metadata['mea:experiment_number'] = token
        elif metadata['mea:plate_number'] is None and re.fullmatch(plate_number_pattern, token):
            metadata['mea:plate_number'] = token
        elif metadata['mea:type_of_experiment'] is None and token.upper() in EXPERIMENT_TYPES:
            metadata['mea:type_of_experiment'] = token
        elif metadata['mea:type_of_cells'] is None and token.lower() in CELL_TYPES:
            metadata['mea:type_of_cells'] = token.lower()
        elif metadata['mea:baseline_exposure'] is None and token.lower() in BASELINE_EXPOSURE:
            metadata['mea:baseline_exposure'] = token.lower()
        elif metadata['mea:type_of_exposure'] is None and token.lower() in EXPOSURE_TYPES:
            metadata['mea:type_of_exposure']  = token.lower()
        elif metadata['mea:compound'] is None and token.lower() in COMPOUNDS:
            metadata['mea:compound'] = token
        elif metadata['mea:sex'] is None and (
            'male' in token.lower()
            or 'female' in token.lower()
            or token.lower().startswith('sex:')
        ):
            metadata['mea:sex'] = token.lower()
        elif metadata['mea:div'] is None and re.match(div_pattern, token, re.IGNORECASE):
            metadata['mea:div'] = token.upper()
        elif (metadata['mea:exposure_duration'] is None and
              (match := re.fullmatch(duration_pattern, token.lower()))):
            metadata['mea:exposure_duration'] = match.group(0)

        else:
                extra_tokens.append(token)

    return extra_tokens

def _check_missing_fields(metadata: Dict[str, Optional[str]], filename: str) -> Optional[str]:
    issues = [f"{key} is missing" for key, value in metadata.items() if value is None]
    if issues:
        header = f"Issues in file: {filename}"
        issue_lines = "\n".join(f"  - {issue}" for issue in issues)
        return f"{header}\n{issue_lines}"
    return None


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)

    examples = [
        "220318_JPW_294001_82-4136_MEA_rCortex_Acute _Exposure_Flunitrazolam_"
        "DIV11_spike Detector (7xSD)(001)(Biocircuit)_neuralMetrics_1200s.csv",
        "231005_LvM_256300_109-1763_MEA_rCortex_Acute_Exposure_DDT_Male_"
        "DIV9(000)(000)_neuralMetrics.csv",
        "201218_LvM_256141_1268-04_rCortex_MEA_Dieldrin_exposure_female"
        "_DIV11(000)_Spike Detector (7 x STD)(000)_neuralMetrics.csv",
        "201218_LvM_256140_1268-20_rCortex_MEA_Lindane_exposure_female"
        "_DIV11(000)_Spike Detector (7 x STD)(000)_neuralMetrics.csv",
        "201112_LvM_256107_1268-20_MEA_rCortex_DDE_baseline_female"
        "_DIV10(000)_Spike Detector (7 x STD)(000)_neuralMetrics.csv",
        "29012025_IT_335001_121-3416_MEA_rCortex_Acute_SnakeVenoms"
        "_noPrewash_Exposure_DIV16(000)(000)_neuralMetrics_1200.csv",
        "260224_IT_XXXXXX_121-3353_MEA_rCortex_Acute_SnakeVenoms_5050"
        "_DIV16_Exposre(000)(000)_neuralMetrics_1200.csv",
        "230907_IDEH_310026_97-4430_MEA_rCortex_Acute_Exposure_"
        "Microplastics_DIV9(000)(000)_neuralMetrics.csv",
        "220825_EEJK_298006_97-4419_MEA_rCortex_Acute_Exposure_"
        "Microplastics_PP_DIV10(000)(000)_neuralMetrics.csv",
        "220825_EEJK_298005_97-4402_MEA_rCortex_Acute_Baseline_"
        "Microplastics_PVC_DIV10(000)(000)_neuralMetrics.csv"
    ]

    for fn in examples:
        print('\n', extract_mea_filename_metadata(fn, check_missing=True), '\n')