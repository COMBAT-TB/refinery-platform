# Create your views here.
from refinery_repository.models import Investigation, Assay, RawData
from django.shortcuts import render_to_response, get_object_or_404
from django.http import HttpResponseRedirect, HttpResponse
from django.template import RequestContext
from django.core.urlresolvers import reverse
from refinery_repository.tasks import call_download, download_ftp_file
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.conf import settings
from celery.task.control import revoke
from celery import states
from celery.result import AsyncResult
import simplejson, re
from core.models import *
from core.tasks import grab_workflows
from django.db import connection
from django.core import serializers
import os, errno
from galaxy_connector.tasks import run_workflow_ui
from galaxy_connector.views import checkActiveInstance, obtain_instance
      

def dictfetchall(cursor):
    "Returns all rows from a cursor as a dict"
    desc = cursor.description
    return [
        dict(zip([col[0] for col in desc], row))
        for row in cursor.fetchall()
    ]

def detail(request, accession):
    i = get_object_or_404(Investigation, pk=accession)
    return render_to_response('refinery_repository/detail.html', {'investigation': i},
                              context_instance=RequestContext(request))
    
def cancelled(request):
    task_ids = request.session['refinery_repository_task_ids']
    for id in task_ids:
        revoke(id, terminate=True)
    return render_to_response('refinery_repository/cancelled.html',
                              context_instance=RequestContext(request))
    
def results(request, accession):
    i = get_object_or_404(Investigation, pk=accession)
    """Returns task status and result in JSON format."""
    task_ids = request.session['refinery_repository_task_ids']
    
    task_progress = list()
    for task_id in task_ids:
        result = AsyncResult(task_id)
        state, retval = result.state, result.result
        response_data = dict(id=task_id, status=state, result=retval)
        if state in states.EXCEPTION_STATES:
            traceback = result.traceback
            response_data.update({"result": safe_repr(retval),
                              "exc": get_full_cls_name(retval.__class__),
                              "traceback": traceback})
                              
        task_progress.append(result.state)
        if(result.state == "PROGRESS"):
            task_progress.append(result.result)
    
    return render_to_response('refinery_repository/results.html', 
                              {
                                'investigation': i, 
                                'task_progress': task_progress
                                },
                              context_instance=RequestContext(request))


def download(request, accession):
    task_ids = list()
    for i in request.POST:
        if re.search('\.zip$', i):
            async_results = call_download(i)
            for ar in async_results:
                task_ids.append(ar.task_id)
        elif re.search('\.gz$', i):
            async_result = download_ftp_file.delay(i, settings.DOWNLOAD_BASE_DIR, accession)
            task_ids.append(async_result.task_id)
    request.session['refinery_repository_task_ids'] = task_ids
    return HttpResponseRedirect(reverse('refinery_repository.views.results', args=(accession,)))


""" Richard's views """
def get_available_files(request):
    """
    Returns all available files to use in workflows
    """
    from django.db import connection
    
    cursor = connection.cursor()
    
    cursor.execute(""" SELECT distinct a.uuid, a.id as assay_id, a.investigation_id, a.assay_name, o.species, d.description, ca.chip_antibody, ab.antibody, t.tissue, g.genotype, r.file, r.raw_data_file FROM
(SELECT distinct on (assay_name) id, assay_uuid as uuid, sample_name, assay_name, investigation_id, study_id from refinery_repository_assay) a
LEFT OUTER JOIN
(SELECT value as species, study_id from refinery_repository_studybracketedfield where sub_type ='ORGANISM') o
ON (a.study_id = o.study_id)
LEFT OUTER JOIN
(SELECT value as description, study_id from refinery_repository_studybracketedfield where sub_type = 'SAMPLE_DESCRIPTION') d
ON (a.study_id = d.study_id)
LEFT OUTER JOIN 
(SELECT assay_id, raw_data_file, file_name as file from refinery_repository_assay_raw_data a JOIN refinery_repository_rawdata b ON a.rawdata_id = b.id) r ON a.id = r.assay_id
LEFT OUTER JOIN
(SELECT value as chip_antibody, assay_id from refinery_repository_assaybracketedfield where sub_type = 'CHIP_ANTIBODY') ca ON a.id = ca.assay_id
LEFT OUTER JOIN
(SELECT value as antibody, assay_id from refinery_repository_assaybracketedfield where sub_type = 'ANTIBODY') as ab ON a.id = ab.assay_id
LEFT OUTER JOIN
(SELECT value as tissue, assay_id from refinery_repository_assaybracketedfield where sub_type = 'TISSUE') as t ON a.id = t.assay_id
LEFT OUTER JOIN
(SELECT value as genotype, assay_id from refinery_repository_assaybracketedfield where sub_type = 'GENOTYPE') as g ON a.id = g.assay_id order by a.investigation_id, a.assay_name""")

    #import pdb; pdb.set_trace()
    
    #field_names = getColumnNamesDict(cursor)
    field_order = getColumnNames(cursor)
    #results = cursor.fetchall() 
    results = dictfetchall(cursor)
    
    #print "field_order"
    #print field_order
    workflows = Workflow.objects.all();

    paginator = Paginator(results, 25) # Show 5 investigations per page

    page = request.GET.get('page', 1)
    try:
        sample_pages = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page.
        sample_pages = paginator.page(1)
    except EmptyPage:
        # If page is out of range (e.g. 9999), deliver last page of results.
        sample_pages = paginator.page(paginator.num_pages)
        
    #return render_to_response('refinery_repository/samples.html', {'fields':field_names, 'order':field_order, 'results': sample_pages}, context_instance=RequestContext(request)) 
    return render_to_response('refinery_repository/samples.html', {'workflows': workflows, 'order':field_order, 'results': sample_pages}, context_instance=RequestContext(request)) 

def get_available_files2(request):
    """
    Returns all available files to use in workflows
    """
    print "refinery_repository.get_available_files2"
    
    cursor = connection.cursor()
    
    cursor.execute(""" SELECT distinct a.uuid, a.id as assay_id, a.investigation_id, a.assay_name, o.species, d.description, ca.chip_antibody, ab.antibody, t.tissue, g.genotype, r.file, r.raw_data_file FROM
(SELECT distinct on (assay_name) id, assay_uuid as uuid, sample_name, assay_name, investigation_id, study_id from refinery_repository_assay) a
LEFT OUTER JOIN
(SELECT value as species, study_id from refinery_repository_studybracketedfield where sub_type ='ORGANISM') o
ON (a.study_id = o.study_id)
LEFT OUTER JOIN
(SELECT value as description, study_id from refinery_repository_studybracketedfield where sub_type = 'SAMPLE_DESCRIPTION') d
ON (a.study_id = d.study_id)
LEFT OUTER JOIN 
(SELECT assay_id, raw_data_file, file_name as file from refinery_repository_assay_raw_data a JOIN refinery_repository_rawdata b ON a.rawdata_id = b.id) r ON a.id = r.assay_id
LEFT OUTER JOIN
(SELECT value as chip_antibody, assay_id from refinery_repository_assaybracketedfield where sub_type = 'CHIP_ANTIBODY') ca ON a.id = ca.assay_id
LEFT OUTER JOIN
(SELECT value as antibody, assay_id from refinery_repository_assaybracketedfield where sub_type = 'ANTIBODY') as ab ON a.id = ab.assay_id
LEFT OUTER JOIN
(SELECT value as tissue, assay_id from refinery_repository_assaybracketedfield where sub_type = 'TISSUE') as t ON a.id = t.assay_id
LEFT OUTER JOIN
(SELECT value as genotype, assay_id from refinery_repository_assaybracketedfield where sub_type = 'GENOTYPE') as g ON a.id = g.assay_id order by a.investigation_id, a.assay_name""")

    #field_names = getColumnNamesDict(cursor)
    field_order = getColumnNames(cursor)
    #results = cursor.fetchall() 
    results = dictfetchall(cursor)
    
    # getting available workflows
    workflows = Workflow.objects.all();

    paginator = Paginator(results, 25) # Show 5 investigations per page

    page = request.GET.get('page', 1)
    try:
        sample_pages = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page.
        sample_pages = paginator.page(1)
    except EmptyPage:
        # If page is out of range (e.g. 9999), deliver last page of results.
        sample_pages = paginator.page(paginator.num_pages)
        
    return render_to_response('refinery_repository/analysis_samples.html', {'workflows':workflows, 'order':field_order, 'results': sample_pages}, context_instance=RequestContext(request)) 

def update_workflows(request):
    """ 
    ajax function for updating available workflows from galaxy 
    """
    print "refinery_repository.update_workflows"
    
    if request.is_ajax():
        # function for updating workflows from galaxy instance
        instance, connection = checkActiveInstance(request)
        grab_workflows(instance, connection)
        # getting updated available workflows
        workflows = Workflow.objects.all()    
        json_serializer = serializers.get_serializer("json")()
        return HttpResponse(json_serializer.serialize(workflows, ensure_ascii=False), mimetype='application/javascript')
    else:
        return HttpResponse(status=400)

def analysis_run(request):
    print "refinery_repository.analysis_run called";
    
    #values = request.POST.getlist('csrfmiddlewaretoken')
    
    # gets workflow_uuid
    workflow_uuid = request.POST.getlist('workflow_choice')[0]
    
    # list of selected assays
    selected_data = [];
    
    for i, val in request.POST.iteritems():
        if (val and val != ""):
            #print "i"
            #print i
            #print val
            if (i.startswith('assay_')):
                selected_data.append({"assay_uuid":i.lstrip('assay_'), 'workflow_input_type':val})
    
    run_info_all = [];
    
    for sd in selected_data:
        curr_assay = Assay.objects.filter(assay_uuid=sd['assay_uuid'])[0];
        curr_rawdata = curr_assay.raw_data.values()[0];
        curr_filename = curr_rawdata['file_name'];
        
        # run_info defines parameters needed to run workflow in galaxy
        run_info = {}
        temp_info = {}
        # full file path
        temp_info['filepath'] = os.path.join(settings.DOWNLOAD_BASE_DIR, curr_assay.investigation_id, curr_filename)
        # Short file name description
        temp_info['filename'] = curr_filename.split('.')[0]
        
        # assay_uid
        temp_info['assay_uuid'] = sd['assay_uuid']
        run_info[sd['workflow_input_type']] = temp_info;
        run_info_all.append(run_info);
        
    # getting current connection to galaxy
    instance, connection = checkActiveInstance(request)
    
    # calling task to setup galaxy workflow and run
    #run_workflow_ui(instance, connection, request, workflow_uuid, run_info_all).delay()
    # To run as background task
    task_result = run_workflow_ui.delay(connection, workflow_uuid, run_info_all)
            
    return render_to_response('refinery_repository/base.html', context_instance=RequestContext(request))

def results_selected(request):
    """Returns task status and result in JSON format."""
    print "refinery_repository.results_selected called";
    task_ids = request.session['refinery_repository_task_ids']
    
    print "task_ids"
    print task_ids;
    
    task_progress = list()
    for task_id in task_ids:
        result = AsyncResult(task_id)
        state, retval = result.state, result.result
        response_data = dict(id=task_id, status=state, result=retval)
        #if state in states.EXCEPTION_STATES:
        #    traceback = result.traceback
        #    response_data.update({"result": safe_repr(retval),
        #                      "exc": get_full_cls_name(retval.__class__),
        #                      "traceback": traceback})
                              
        #task_progress.append(result.state)
        dictionary = dict()
        if(result.state == "PROGRESS"):    
            dictionary = result.result
        
        dictionary['task_id'] = task_id
        dictionary['state'] = state
        task_progress.append(dictionary)
        
        """
        print "results"
        print result    
        print "dict"
        print dictionary
        """
        
    if (request.is_ajax()):
        print "RETURNING AJAX"
        print task_progress;
        #return HttpResponse(simplejson.dumps({'task_progress': task_progress}, ensure_ascii=False), mimetype='application/javascript')
        return HttpResponse(simplejson.dumps( task_progress, ensure_ascii=False), mimetype='application/javascript')
    
    else:
        print "NOT AJAX"
        return render_to_response('refinery_repository/results_download.html', { 'task_progress': task_progress }, context_instance=RequestContext(request))

""" 
Function for dealing w/ selected samples to download and input into workflow
"""

def download_selected_samples(request):
    print "refinery_repository.download_selected_samples called";
    print request.POST;
    
    task_ids = list()
    
    values = request.POST.getlist('csrfmiddlewaretoken')
    print "values"
    print values;
    print len(values)
    
    #for i in request.POST:
    for i, val in request.POST.iteritems():
        print "i"
        print i
        print val
        if re.search('\.zip$', i):
            async_results = call_download(i)
            for ar in async_results:
                task_ids.append(ar.task_id)
        elif re.search('\.gz$', i):
            accession, new_i = i.split(',');
            print new_i
            print accession
            async_result = download_ftp_file.delay(new_i, settings.DOWNLOAD_BASE_DIR, accession)
            task_ids.append(async_result.task_id)
            
            #id = download_ftp_file.delay(new_i, settings.DOWNLOAD_BASE_DIR, accession)
            #task_ids.append(id)
    print "task_ids";
    print task_ids;
    request.session['refinery_repository_task_ids'] = task_ids
    return HttpResponseRedirect(reverse('refinery_repository.views.results_selected', args=()))
    #return HttpResponseRedirect(reverse('refinery_repository.views.results', args=(accession,)))

"""
Function for AJAX returning WorkflowDataInputMap for a specified workflow_uuid
"""
def getWorkflowDataInputMap(request, workflow_uuid):
    print "refinery_repository.getWorkflowDataInputMap called";
        
    curr_workflow = Workflow.objects.filter(uuid=workflow_uuid)[0]
    data = serializers.serialize('json',curr_workflow.data_inputs.all())
    
    if request.is_ajax():
        return HttpResponse(data, mimetype='application/javascript')
    else:
        return HttpResponse(data,mimetype='application/json')
    
"""
Helper function for returning rawsql as a dictionary object
"""
def dictfetchall(cursor):
    "Returns all rows from a cursor as a dict"
    desc = cursor.description
    return [
        dict(zip([col[0] for col in desc], row))
        for row in cursor.fetchall()
    ]

"""
Returns column names for a given raw sql call
"""
def getColumnNamesDict(cursor):
    field_names = {}
    count = 0
    for fn in cursor.description:
        #field_names.append(fn[0]);
        field_names[fn[0]] = count 
        count += 1
    return field_names

def getColumnNames(cursor):
    field_names = [];
    for fn in cursor.description:
        field_names.append(fn[0]);
    return field_names
