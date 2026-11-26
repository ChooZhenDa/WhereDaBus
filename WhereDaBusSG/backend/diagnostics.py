"""Read-only diagnostics for static-feed coverage and route sequences."""
from collections import Counter, defaultdict
from .domain import validate_static, retain_available_routes, service_no, _static_integer


def _direction_keys(rows):
    """Match the importer's canonical identity; invalid rows fail validation below."""
    keys = set()
    for row in rows:
        try:
            number = service_no(row['ServiceNo'])
            direction = _static_integer(row['Direction'], 'service direction')
            if direction not in (1,2):
                continue
        except (ValueError,KeyError,TypeError,ArithmeticError):
            continue
        keys.add((number,direction))
    return keys

def sequence_issues(routes, max_examples=20):
    """Report all affected groups, with bounded examples even for very large gaps."""
    grouped = defaultdict(list)
    invalid = []
    for index, row in enumerate(routes):
        try:
            number = service_no(row['ServiceNo'])
            raw_direction = row['Direction']
            direction = int(raw_direction)
            if isinstance(raw_direction,bool) or float(raw_direction) != direction or direction not in (1,2):
                raise ValueError()
            raw = row['StopSequence']
            sequence = int(raw)
            if isinstance(raw,bool) or float(raw) != sequence or sequence <= 0:
                raise ValueError()
            grouped[(number,direction)].append(sequence)
        except (KeyError,TypeError,ValueError,OverflowError):
            if len(invalid) < max_examples:
                invalid.append({'rowIndex':index,'error':'Invalid service, direction or stop sequence'})
    result = []
    for (number,direction), sequences in sorted(grouped.items()):
        counts = Counter(sequences)
        ordered = sorted(counts)
        missing, gap_count, previous = [],0,0
        for sequence in ordered:
            if sequence > previous+1:
                gap_count += 1
                if len(missing) < max_examples:
                    missing.append({'from':previous+1,'to':sequence-1})
            previous = sequence
        duplicates = sorted(sequence for sequence,count in counts.items() if count > 1)
        if gap_count or duplicates:
            result.append({'serviceNo':number,'direction':direction,
                           'rowCount':len(sequences),'firstSequence':ordered[0],
                           'lastSequence':ordered[-1],'missingRanges':missing,
                           'missingRangeCount':gap_count,
                           'duplicateSequences':duplicates[:max_examples],
                           'duplicateSequenceCount':len(duplicates)})
    return {'routeSequenceIssueCount':len(result),'sequenceIssues':result[:max_examples],
            'examplesTruncated':len(result)>max_examples,'invalidRowExamples':invalid}

def diagnose_static(client):
    """Fetch public infrastructure data; report problems without opening a database."""
    snapshot = {name:client.all_rows(name) for name in ('BusStops','BusServices','BusRoutes')}
    stops, services, routes = (snapshot[name] for name in ('BusStops','BusServices','BusRoutes'))
    report = {'stops':len(stops),'serviceDirections':len(services),'routeOccurrences':len(routes),
              **sequence_issues(routes)}
    service_keys = _direction_keys(services)
    route_keys = _direction_keys(routes)
    report['missingRouteDirections'] = [
        {'serviceNo':number,'direction':direction} for number,direction in sorted(service_keys-route_keys)]
    try:
        validate_static(stops,services,routes)
    except (ValueError,KeyError,TypeError,ArithmeticError) as error:
        report['validForFullImport'] = False
        report['validationError'] = str(error)
    else:
        report['validForFullImport'] = True
        report['validationError'] = None
    # A failed strict sequence check need not prevent the coverage-aware
    # importer from retaining structurally valid published route occurrences.
    try:
        retained, _, partial = retain_available_routes(stops,services,routes)
    except (ValueError,KeyError,TypeError,ArithmeticError) as error:
        report['validForCoverageImport'] = False
        report['coverageValidationError'] = str(error)
        report['retainedRouteOccurrences'] = 0
    else:
        report['validForCoverageImport'] = True
        report['coverageValidationError'] = None
        report['retainedRouteOccurrences'] = len(retained)
        report['partialDirectionCount'] = len(partial)
    report['databaseChanged'] = False
    return report
