"""MongoDB 7.0+ pipelines; dates passed as BSON datetimes by PyMongo."""
from datetime import datetime, timedelta, timezone

SG = timezone(timedelta(hours=8))

def previous_seven_days(now=None):
    """Seven completed Singapore calendar days, excluding the partial current day."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Use an aware timestamp')
    end = now.astimezone(SG).replace(hour=0,minute=0,second=0,microsecond=0)
    return (end-timedelta(days=7)).astimezone(timezone.utc), end.astimezone(timezone.utc)

def base_pipeline(service, start, end, stop=None, morning=False):
    if end <= start or end-start > timedelta(days=31):
        raise ValueError('Query range must be positive and at most 31 days')
    match = {'meta.serviceNo':service, 'observedAt':{'$gte':start,'$lt':end}}
    if stop:
        match['meta.stopCode'] = stop
    stages = [
        {'$match':match},
        # Retried inserts contain the exact same durable-inbox observation.
        {'$group':{'_id':'$logicalKey','doc':{'$first':'$$ROOT'}}},
        {'$replaceRoot':{'newRoot':'$doc'}},
        {'$unwind':'$arrivals'},
        {'$match':{'arrivals.rank':1}},
    ]
    if morning:
        stages += [
            {'$set':{
                'sgHour':{'$hour':{'date':'$observedAt','timezone':'Asia/Singapore'}},
                'sgDay':{'$isoDayOfWeek':{'date':'$observedAt','timezone':'Asia/Singapore'}}}},
            {'$match':{'sgHour':{'$gte':7,'$lt':9},'sgDay':{'$lte':5}}},
        ]
    # Do not treat unknown or schedule-only loads as observed occupancy.
    stages += [{'$set':{
        'localDate':{'$dateToString':{'date':'$observedAt','format':'%Y-%m-%d',
                                     'timezone':'Asia/Singapore'}},
        'eligible':{'$and':[
            {'$eq':['$arrivals.monitored',1]},
            {'$ne':['$arrivals.eta',None]},
            {'$gte':['$arrivals.etaSeconds',0]},
            {'$lte':['$arrivals.etaSeconds',1800]},
            {'$in':['$arrivals.load',['SEA','SDA','LSD']]},
        ]}
    }}]
    return stages

def counters(group_id):
    return {'$group':{
        '_id':group_id,
        'observations':{'$sum':1},
        'validSamples':{'$sum':{'$cond':['$eligible',1,0]}},
        'scoreTotal':{'$sum':{'$cond':['$eligible','$arrivals.loadScore',0]}},
        'seatsAvailable':{'$sum':{'$cond':[{'$and':['$eligible',{'$eq':['$arrivals.load','SEA']}]},1,0]}},
        'standingAvailable':{'$sum':{'$cond':[{'$and':['$eligible',{'$eq':['$arrivals.load','SDA']}]},1,0]}},
        'limitedStanding':{'$sum':{'$cond':[{'$and':['$eligible',{'$eq':['$arrivals.load','LSD']}]},1,0]}},
        'sampleDates':{'$addToSet':'$localDate'},
        'validDates':{'$addToSet':{'$cond':['$eligible','$localDate',None]}},
    }}

def safe_ratio(numerator, denominator, scale=1):
    return {'$cond':[{'$gt':[denominator,0]},
                     {'$multiply':[scale,{'$divide':[numerator,denominator]}]},None]}

def metrics():
    return {'$set':{
        'meanCrowdingOrdinal':safe_ratio('$scoreTotal','$validSamples'),
        'seatsAvailablePct':safe_ratio('$seatsAvailable','$validSamples',100),
        'standingAvailablePct':safe_ratio('$standingAvailable','$validSamples',100),
        'limitedStandingPct':safe_ratio('$limitedStanding','$validSamples',100),
        'validSamplePct':safe_ratio('$validSamples','$observations',100),
        'daysWithSamples':{'$size':'$sampleDates'},
        'daysWithValidSamples':{'$size':{'$setDifference':['$validDates',[None]]}},
    }}

def morning_peak(service, start, end, stop=None):
    """Q1: weekday 07:00-08:59 SG; each stop/operator is reported separately."""
    return base_pipeline(service,start,end,stop,morning=True) + [
        counters({'stopCode':'$meta.stopCode','serviceNo':'$meta.serviceNo','operator':'$meta.operator'}),
        metrics(), {'$unset':['sampleDates','validDates']}, {'$sort':{'_id.stopCode':1}},
    ]

def half_hour_trend(service, start, end, stop):
    """Q2: weighted rolling mean across current + two preceding OBSERVED bins.

    Gaps remain gaps; no imputed empty bins or claims of elapsed-hour coverage.
    """
    stages = base_pipeline(service,start,end,stop)
    stages += [{'$set':{'bucket':{'$dateTrunc':{'date':'$observedAt','unit':'minute',
                           'binSize':30,'timezone':'Asia/Singapore'}}}},
               counters({'bucket':'$bucket','operator':'$meta.operator'}), metrics(),
               {'$setWindowFields':{
                   'partitionBy':'$_id.operator','sortBy':{'_id.bucket':1},
                   'output':{
                       'rollingScore':{'$sum':'$scoreTotal','window':{'documents':[-2,0]}},
                       'rollingValid':{'$sum':'$validSamples','window':{'documents':[-2,0]}},
                   }}},
               {'$set':{'rollingMeanOrdinal':safe_ratio('$rollingScore','$rollingValid')}},
               {'$unset':['sampleDates','validDates']},
               {'$sort':{'_id.bucket':1,'_id.operator':1}}]
    return stages

def crowded_stops(service,start,end,min_samples=30):
    """Q3: rank stop/operator cohorts with >=3 valid dates and adequate samples."""
    return morning_peak(service,start,end)[:-1] + [
        {'$match':{'validSamples':{'$gte':min_samples},'daysWithValidSamples':{'$gte':3}}},
        {'$setWindowFields':{'sortBy':{'limitedStandingPct':-1},
                            'output':{'crowdingRank':{'$denseRank':{}}}}},
        {'$sort':{'crowdingRank':1,'_id.stopCode':1}}, {'$limit':10}]
