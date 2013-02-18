#!/usr/bin/env python
# -*- Mode: python; tab-width: 4; indent-tabs-mode: nil; indent-offset: 4 -*-
import os.path
import argparse
import yaml
import numpy as np
from collections import defaultdict
import datetime

import roslib; roslib.load_manifest('flyvr')
roslib.load_manifest('visualization_msgs')
roslib.load_manifest('camera_calibration')
import camera_calibration.calibrator

import rospy

import flyvr.rviz_utils
import std_msgs.msg
from geometry_msgs.msg import Pose2D
from sensor_msgs.msg import CameraInfo
import tf.broadcaster
from visualization_msgs.msg import MarkerArray

import simple_geom
import camera_model
import dlt
import display_client

import rosgobject.core
import rosgobject.wrappers
from gi.repository import Gtk, GObject
from fit_extrinsics import fit_extrinsics, fit_extrinsics_iterative

def nice_float_fmt(treeviewcolumn, cell, model, iter, column):
    float_in = model.get_value(iter, column)
    cell.set_property('text', '%g'%float_in )

FULLSCREEN='FULL_SCREEN'

# columns in self.point_store
VDISP=0
TEXU=1
TEXV=2
DISPLAYX=3
DISPLAYY=4
SHOWPT=5
JOYLISTEN=6

# columns in self.vdisp_store
VS_VDISP = 0
VS_COUNT = 1
VS_CAL_BUTTON = 2
VS_MRE = 3
VS_SHOW_BEACHBALL = 4
VS_CAMERA_OBJECT = 5
VS_PUBLISH_RVIZ = 6

def pretty_intrinsics_str(cam):
    K = cam.K
    d = cam.distortion
    dstr = ' '.join(['% 3g'%di for di in d[:,0]])
    args = tuple(list(K.ravel()) + [dstr])#str(d[:,0])])
    result = \
"""K: % 10g % 10g % 10g
   % 10g % 10g % 10g
   % 10g % 10g % 10g
distortion: %s"""%args
    return result

def get_camera_for_boards(rows,width=0,height=0):
    info_dict = {}
    for row in rows:
        r = row[0]
        info_str = '%d %d %f'%(r['rows'], r['columns'], r['size'])
        if info_str not in info_dict:
            # create entry
            info = camera_calibration.calibrator.ChessboardInfo()
            info.dim = r['size']
            info.n_cols = r['columns']
            info.n_rows = r['rows']
            info_dict[info_str] = {'info':info,
                                   'corners':[]}
        this_list = info_dict[info_str]['corners']
        this_corners = r['points']
        assert len(this_corners)==r['rows']*r['columns']
        this_list.append( this_corners )

    boards = []
    goodcorners = []
    for k in info_dict:
        info = info_dict[k]['info']
        for xys in info_dict[k]['corners']:
            goodcorners.append( (xys,info) )

    cal = camera_calibration.calibrator.MonoCalibrator(boards)
    cal.size = (width,height)
    r = cal.cal_fromcorners(goodcorners)
    msg = cal.as_message()

    buf = roslib.message.strify_message(msg)
    obj = yaml.load(buf)
    cam = camera_model.load_camera_from_dict(obj,
                                             extrinsics_required=False)
    return cam

class CheckerboardPlotWidget(Gtk.DrawingArea):
    def __init__(self):
        super(CheckerboardPlotWidget,self).__init__()
        self.set_size_request(100,80)
        self.connect('draw', self._on_draw_event)
        self.connect('configure-event', self._on_configure_event)
        self._surface = None
        print 'CheckerboardPlotWidget.__init__'

    def _on_draw_event(self, widget, cr):
        print 'draw event!'
        cr.set_source_surface(self._surface, 0.0, 0.0)
        cr.paint()
        if 1:
            return

        vec,pts = self.get_vec_and_points()

        if vec is not None:
            cr.set_source_rgb (1, 0, 0)
            cr.set_line_width (1)
            cr.move_to(vec.p.x,vec.p.y)
            cr.line_to(vec.p2.x,vec.p2.y)
            cr.stroke()

        for pt,rgb in pts:
            if pt is not None:
                cr.set_source_rgb (*rgb)
                cr.move_to(pt.x, pt.y)
                cr.arc(pt.x, pt.y, 2, 0, 2.0 * math.pi)
                cr.fill()

    def _on_configure_event(self, widget, event):
        print 'CheckerboardPlotWidget.config'

        allocation = self.get_allocation()
        self._surface = self.get_window().create_similar_surface(
                                            cairo.CONTENT_COLOR,
                                            allocation.width,
                                            allocation.height)

        self._draw_background()

    def _draw_background(self):
        print 'CheckerboardPlotWidget.bg'
        cr = cairo.Context(self._surface)
        cr.set_source_rgb(1, 1, 0)
        cr.paint()

        # cr.set_source_rgb (0, 0, 0)
        # cr.set_line_width (1)
        # cx,cy = self._xy_to_pxpy(0,0)
        # cr.arc(cx, cy, self._r_px, 0, 2.0 * math.pi)
        # cr.stroke()

class UI:
    def __init__(self, dsc, geom):
        self.display_intrinsic_cam = None
        self.dsc = dsc

        rosgobject.core.SubscriberGObject('joy_click_pose', Pose2D).connect('message', self.on_joy_callback)

        self.data_filename = None
        self.yamlfilter = Gtk.FileFilter()
        self.yamlfilter.set_name("YAML Files")
        self.yamlfilter.add_pattern("*.yaml")

        self.geom = geom

        me = os.path.dirname(os.path.abspath(__file__))
        ui_fname = os.path.join(me,"pinhole-calibration-wizard.ui")

        self._ui = Gtk.Builder()
        self._ui.add_from_file(ui_fname)

        self.joy_mode=None

        self.intr_pub = {}
        self.frustum_pub = {}
        self._build_ui()

        a1 = self._ui.get_object('main_window')
        a1.connect("delete-event", rosgobject.main_quit)
        a1.show_all()

        self.tf_b = tf.broadcaster.TransformBroadcaster()
        GObject.timeout_add(100, self.on_timer)

    def on_joy_callback(self, widget, msg):
        if self.joy_mode=='do CK':
            pt = [ msg.x,  msg.y ]
            self._current_checkerboard['points'].append( pt )

            label = self._ui.get_object('N_CKB_points_label')
            label.set_text( '%d'%len(self._current_checkerboard['points']))

        elif self.joy_mode=='do points':
            if not len(self.point_store):
                return

            for row in self.point_store:
                if row[JOYLISTEN]:
                    break

            if not row[JOYLISTEN]:
                # no row selected
                return

            row[DISPLAYX] = msg.x
            row[DISPLAYY] = msg.y
            self.update_bg_image()

    def _build_ui(self):
        # build main window ----------------------

        window = self._ui.get_object('main_box')

        nb = Gtk.Notebook()
        window.add(nb)

        nb.append_page(self._ui.get_object('checkerboard_grid'),
                       Gtk.Label(label='intrinsics'))

        nb.append_page(Gtk.VBox(),
                       Gtk.Label(label='virtual displays'))

        nb.append_page(self._ui.get_object('corresponding_points_grid'),
                       Gtk.Label(label='extrinsics'))

        self._ui.get_object('file_open_menu_item').connect(
            'activate', self.on_open)

        self._ui.get_object('file_save_menu_item').connect(
            'activate', self.on_save)

        self._ui.get_object('file_saveas_menu_item').connect(
            'activate', self.on_save_as)

        self._ui.get_object('file_quit_menu_item').connect(
            'activate', rosgobject.main_quit)

        self._ui.get_object('help_about_menu_item').connect(
            'activate', self.on_help_about)

        # setup checkerboard treeview ----------------

        self.checkerboard_store = Gtk.ListStore(object)

        treeview = self._ui.get_object('checkerboard_treeview')
        treeview.set_model( self.checkerboard_store )

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("N rows", renderer)
        column.set_cell_data_func(renderer, self.render_checkerboard_row, func_data='rows')
        treeview.append_column(column)

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("N columns", renderer)
        column.set_cell_data_func(renderer, self.render_checkerboard_row, func_data='columns')
        treeview.append_column(column)

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("size", renderer)
        column.set_cell_data_func(renderer, self.render_checkerboard_row, func_data='size')
        treeview.append_column(column)

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("time", renderer)
        column.set_cell_data_func(renderer, self.render_checkerboard_row, func_data='date_string')
        treeview.append_column(column)

        self._ui.get_object('CK_add_button').connect('clicked', self.on_CK_add)
        self._ui.get_object('CK_remove_button').connect('clicked', self.on_CK_remove)

        self._ui.get_object('compute_intrinsics').connect('clicked', self.on_compute_intrinsics)

        # setup checkerboard dialog ----------------

        self.add_CK_dialog = Gtk.Dialog(title="Add checkerboard",
                            parent=None,
                            buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK,
                                     Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL))
        grid = self._ui.get_object('add_CK_dialog_grid')
        if 1:
            grid.attach(  Gtk.Label(label='in grid2'),
                          0, 5, 1, 1)
            #grid.add(  Gtk.Label(label='in grid2'))
            self.add_CK_dialog.get_content_area().add(grid)
        if 0:

            box = self._ui.get_object('checkerboard_plot_box')
            box.add( Gtk.Label(label='before'))
            box.add( CheckerboardPlotWidget())
            box.add( Gtk.Label(label='after'))
            print 'build plot widget'

            #self._ui.get_object('add_CK_dialog_grid').add(  Gtk.Label(label='in grid'))

        # setup help->about dialog -----------------
        self.help_about_dialog = Gtk.Dialog(title='About',
                                            parent=None,
                                            buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK))
        self.help_about_dialog.get_content_area().add(self._ui.get_object('help_about_dialog_grid'))
        version_str = getattr(flyvr,'__version__','0.0')
        self._ui.get_object('version_label').set_text(version_str)

        # setup vdisp combobox ----------------
        di = self.dsc.get_display_info()
        self.vdisp_store = Gtk.ListStore(str,int,bool,float,bool,object,bool)

        if 'virtualDisplays' in di:
            vdisps = [d['id'] for d in di['virtualDisplays']]
        else:
            vdisps = [FULLSCREEN]

        for vdisp in vdisps:
            self.vdisp_store.append([vdisp,0,0,np.nan,0,None,0])
            self.intr_pub[vdisp] = rospy.Publisher(vdisp+'/camera_info',
                                                   CameraInfo, latch=True)
            self.frustum_pub[vdisp] = rospy.Publisher(vdisp+'/frustum',
                                                       MarkerArray)

        # create vdisp treeview -----------------------

        treeview = self._ui.get_object('vdisp_treeview')
        treeview.set_model( self.vdisp_store )

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("virtual display", renderer, text=VS_VDISP)
        column.set_sort_column_id(VS_VDISP)
        treeview.append_column(column)

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("count", renderer, text=VS_COUNT)
        column.set_sort_column_id(VS_COUNT)
        treeview.append_column(column)

        renderer = Gtk.CellRendererToggle()
        renderer.connect("toggled", self.on_trigger_cal)
        column = Gtk.TreeViewColumn("calibrate", renderer, active=VS_CAL_BUTTON)
        treeview.append_column(column)

        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("mean reproj. error", renderer,
                                    text=VS_MRE)
        column.set_sort_column_id(VS_MRE)
        treeview.append_column(column)

        renderer = Gtk.CellRendererToggle()
        renderer.connect("toggled", self.on_toggle_show_beachball)
        column = Gtk.TreeViewColumn("show beachball", renderer, active=VS_SHOW_BEACHBALL)
        treeview.append_column(column)

        renderer = Gtk.CellRendererToggle()
        renderer.connect("toggled", self.on_toggle_publish_rviz)
        column = Gtk.TreeViewColumn("publish RViz cam", renderer, active=VS_PUBLISH_RVIZ)
        treeview.append_column(column)

        # create point treeview -----------------------

        self.point_store = Gtk.ListStore(str, float, float, float, float, bool, bool)

        treeview = self._ui.get_object('treeview1')
        treeview.set_model( self.point_store )

        renderer_text = Gtk.CellRendererCombo(model=self.vdisp_store,
                                              text_column=VDISP,
                                              editable=True)
        renderer_text.connect("edited", self.on_edit_vdisp, VDISP)
        column = Gtk.TreeViewColumn("virtual display", renderer_text, text=VDISP)
        column.set_sort_column_id(VDISP)
        treeview.append_column(column)

        renderer_text = Gtk.CellRendererText(editable=True)
        renderer_text.connect("edited", self.on_edit_cell, TEXU)
        column = Gtk.TreeViewColumn("texture U", renderer_text, text=TEXU)
        column.set_cell_data_func(renderer_text, nice_float_fmt, func_data=TEXU)
        column.set_sort_column_id(TEXU)
        treeview.append_column(column)

        renderer_text = Gtk.CellRendererText(editable=True)
        renderer_text.connect("edited", self.on_edit_cell, TEXV)
        column = Gtk.TreeViewColumn("texture V", renderer_text, text=TEXV)
        column.set_cell_data_func(renderer_text, nice_float_fmt, func_data=TEXV)
        column.set_sort_column_id(TEXV)
        treeview.append_column(column)

        renderer_text = Gtk.CellRendererText(editable=True)
        renderer_text.connect("edited", self.on_edit_cell, DISPLAYX)
        column = Gtk.TreeViewColumn("display X", renderer_text, text=DISPLAYX)
        column.set_cell_data_func(renderer_text, nice_float_fmt, func_data=DISPLAYX)
        column.set_sort_column_id(DISPLAYX)
        treeview.append_column(column)

        renderer_text = Gtk.CellRendererText(editable=True)
        renderer_text.connect("edited", self.on_edit_cell, DISPLAYY)
        column = Gtk.TreeViewColumn("display Y", renderer_text, text=DISPLAYY)
        column.set_cell_data_func(renderer_text, nice_float_fmt, func_data=DISPLAYY)
        column.set_sort_column_id(DISPLAYY)
        treeview.append_column(column)

        renderer_toggle = Gtk.CellRendererToggle()
        renderer_toggle.connect("toggled", self.on_toggle_point_show)
        column = Gtk.TreeViewColumn("show", renderer_toggle, active=SHOWPT)
        column.set_sort_column_id(SHOWPT)
        treeview.append_column(column)

        renderer_pixbuf = Gtk.CellRendererToggle()
        renderer_pixbuf.set_radio(True)
        renderer_pixbuf.connect("toggled", self.on_do_point)
        column = Gtk.TreeViewColumn('Joystick select', renderer_pixbuf, active=JOYLISTEN)
        treeview.append_column(column)

        self.point_store.connect("row-changed",  self.on_points_updated)
        self.point_store.connect("row-inserted", self.on_points_updated)
        self.point_store.connect("row-deleted",  self.on_points_updated)

        # connect treeview buttons ---------------------------
        self._ui.get_object('UV_add_button').connect('clicked', self.on_add_UV)
        self._ui.get_object('UV_remove_button').connect('clicked', self.on_remove_UV)

        # self._ui.get_object('save_points_button').connect('clicked', self.on_save_to_yaml,
        #                                                   self.point_store_to_list)
        # self._ui.get_object('load_points_button').connect('clicked', self.on_load_points_button)

        self._ui.get_object('show_all_button').connect('clicked', self.on_show_all_button, True)
        self._ui.get_object('show_none_button').connect('clicked', self.on_show_all_button, False)

        # setup ComboBoxText
        cal_method_cbtext = self._ui.get_object('cal_method_cbtext')

        cal_method_cbtext.append('iterative extrinsic only','iterative extrinsic only')
        cal_method_cbtext.append('extrinsic only','extrinsic only')
        cal_method_cbtext.append('DLT','DLT')
        cal_method_cbtext.append('RANSAC DLT','RANSAC DLT')
        cal_method_cbtext.set_active_id('extrinsic only')

    # File menu ----------------------------------------------------
    def _load_from_file( self, fname ):
        with open(fname,mode='r') as fd:
            buf = fd.read()

        obj = yaml.safe_load(buf)
        self.load_corresponding_points(obj)
        self.load_checkerboards(obj)
        self.data_filename = fname

    def _save_to_file( self, fname ):
        obj = self.checkerboard_store_to_list()
        d2 = self.point_store_to_list()
        obj.update( d2 )

        buf = yaml.dump(obj)
        with open(fname,mode='w') as fd:
            fd.write(buf)
        self.data_filename = fname

    def on_open(self, button):
        filechooserdialog = Gtk.FileChooserDialog(title="FileChooserDialog",
                                                  parent=None,
                                                  buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK,
                                                           Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL))

        try:
            response = filechooserdialog.run()
            if response == Gtk.ResponseType.OK:
                self._load_from_file( filechooserdialog.get_filename() )
        finally:
            filechooserdialog.destroy()

    def on_save(self, *args):
        if self.data_filename is None:
            return self.on_save_as(*args)
        self._save_to_file( self.data_filename )

    def on_save_as(self, *args):
        d1 = self.checkerboard_store_to_list()
        d2 = self.point_store_to_list()
        d1.update( d2 )

        filechooserdialog = Gtk.FileChooserDialog(title="FileChooserDialog",
                                                  parent=None,
                                                  action=Gtk.FileChooserAction.SAVE,
                                                  buttons=(Gtk.STOCK_OK, Gtk.ResponseType.OK,
                                                           Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL))
        filechooserdialog.add_filter( self.yamlfilter )
        filechooserdialog.set_do_overwrite_confirmation(True)

        try:
            response = filechooserdialog.run()

            if response == Gtk.ResponseType.OK:
                fname = filechooserdialog.get_filename()
                self._save_to_file( fname )
        finally:
            filechooserdialog.destroy()

    # ---------------- Help menu -----------------------------
    def on_help_about(self,*args):
        try:
            self.help_about_dialog.run()
        finally:
            self.help_about_dialog.hide()

    # ---------------- Checkerboard & intrinsics -------------

    def checkerboard_store_to_list(self):
        result = [row[0] for row in self.checkerboard_store]
        return {'checkerboards':result}

    def load_checkerboards(self,indict):
        in_list = indict.get('checkerboards',[])
        self.checkerboard_store.clear()
        for rowdict in in_list:
            self.checkerboard_store.append( [rowdict] )

    def render_checkerboard_row(self, treeviewcolumn, cell, model, iter, attr):
        rowdict = model.get_value(iter,0)
        cell.set_property('text', str(rowdict[attr] ))

    def on_CK_add(self, *args):
        self.joy_mode='do CK'
        self._current_checkerboard = {'points':[]}
        try:
            response = self.add_CK_dialog.run()

            if response == Gtk.ResponseType.OK:
                m = self._ui.get_object('CK_n_rows_adjustment')
                self._current_checkerboard['rows'] = int(m.get_value())

                m = self._ui.get_object('CK_n_cols_adjustment')
                self._current_checkerboard['columns'] = int(m.get_value())

                e = self._ui.get_object('CK_size_entry')
                size = float( e.get_text())
                self._current_checkerboard['size'] = size
                nowstr = datetime.datetime.now().isoformat(' ')
                self._current_checkerboard['date_string'] = nowstr
                self.checkerboard_store.append( [self._current_checkerboard] )
            self.joy_mode=None
            self._current_checkerboard = None # delete current checkerboard
        finally:
            self.add_CK_dialog.hide()

        label = self._ui.get_object('N_CKB_points_label')
        label.set_text('')

    def on_CK_remove(self, *args):
        treeview = self._ui.get_object('checkerboard_treeview')
        selection = treeview.get_selection()
        sel = selection.get_selected()
        if not sel[1] == None:
            self.checkerboard_store.remove( sel[1] )

    def on_compute_intrinsics(self,*args):
        rows = [r for r in self.checkerboard_store]
        cam = get_camera_for_boards( rows,
                                     width=self.dsc.width,
                                     height=self.dsc.height )
        self.display_intrinsic_cam = cam
        self._ui.get_object('intrinsic_status_label').set_text(pretty_intrinsics_str(cam))

        # if 1:
        #     print 'all ------------------'
        #     print pretty_intrinsics_str(cam)

        #     for i in range(len(rows)):
        #         r2 = [r for (j,r) in enumerate(rows) if j!=i]

        #         cam2 = get_camera_for_boards( r2,
        #                                       width=self.dsc.width,
        #                                       height=self.dsc.height )

        #         print 'not %d (%s) ------------------'%(i, rows[i][0]['date_string'])
        #         print pretty_intrinsics_str(cam2)

    # ---------------- Point correspondence & extrinsics -------------

    def on_show_all_button(self,*args):
        val = args[-1]
        for row in self.point_store:
            row[SHOWPT] = val
        self.update_bg_image()

    def on_points_updated(self, *args):
        vdisps = defaultdict(int)
        for row in self.point_store:
            vdisps[ row[VDISP] ] += 1

        for row in self.vdisp_store:
            vdisp = row[VS_VDISP]
            row[VS_COUNT] = vdisps[ vdisp ]

    def on_edit_vdisp(self,widget,path,textval,colnum):
        self.point_store[path][colnum] = textval
        self.update_bg_image()

    def on_edit_cell(self,widget,path,textval,colnum):
        value = float(textval)
        self.point_store[path][colnum] = value
        self.update_bg_image()

    def on_do_point(self, widget, path):
        selected_path = Gtk.TreePath(path)
        # perform mutually-exclusive radio button setting
        for row in self.point_store:
            row[JOYLISTEN] = (row.path == selected_path)

    def _get_default_vdisp(self):
        di = self.dsc.get_display_info()
        if 'virtualDisplays' in di:
            val = di['virtualDisplays'][0]['id']
        else:
            val = FULLSCREEN
        return val

    def point_store_to_list(self):
        result = []
        for row in self.point_store:
            rowdict = dict(
                virtual_display = row[VDISP],
                texture_u = row[TEXU],
                texture_v = row[TEXV],
                display_x = row[DISPLAYX],
                display_y = row[DISPLAYY],
                )
            result.append( rowdict )
        r = {'uv_display_points':result}
        return r

    def load_corresponding_points(self,indict):
        in_list = indict.get('uv_display_points',[])
        self.point_store.clear()
        for rowdict in in_list:
            self._add_pt_entry( rowdict['virtual_display'],
                                rowdict['texture_u'],
                                rowdict['texture_v'],
                                displayX=rowdict['display_x'],
                                displayY=rowdict['display_y'],
                                )
        self.update_bg_image()

    def on_add_UV(self, button):
        vdisp = self._get_default_vdisp()
        self._add_pt_entry(vdisp, np.nan, np.nan )
        self.update_bg_image()

    def on_remove_UV(self,button):
        treeview = self._ui.get_object('treeview1')
        selection = treeview.get_selection()
        sel = selection.get_selected()
        if not sel[1] == None:
            self.point_store.remove( sel[1] )

    def _add_pt_entry(self, vdisp, texU, texV,
                      displayX=float(np.nan), displayY=float(np.nan),
                      show_point=True, joy_listen=False):
        self.point_store.append([vdisp, texU, texV,
                               displayX, displayY,
                               show_point, joy_listen])

    def on_toggle_point_show(self, widget, path):
        self.point_store[path][SHOWPT] = not self.point_store[path][SHOWPT]
        self.update_bg_image()

    def on_toggle_show_beachball(self, widget, path):
        self.vdisp_store[path][VS_SHOW_BEACHBALL] = not self.vdisp_store[path][VS_SHOW_BEACHBALL]
        self.update_bg_image()

    def on_toggle_publish_rviz(self, widget, path):
        self.vdisp_store[path][VS_PUBLISH_RVIZ] = not self.vdisp_store[path][VS_PUBLISH_RVIZ]
        self.publish_rviz()

    def on_timer(self):
        self.publish_rviz()
        return True

    def publish_rviz(self):
        now = rospy.Time.now()
        future = now + rospy.Duration(0.010) # 10 msec in future

        #all_cam_id_base = 100300
        all_cam_id_base = 0
        for enum,row in enumerate(self.vdisp_store):
            vdisp = row[VS_VDISP]

            frame_id = '/'+vdisp

            cam = row[VS_CAMERA_OBJECT]
            cam_id_base = enum*100 + all_cam_id_base
            if cam is not None and row[VS_PUBLISH_RVIZ]:

                intrinsic_msg = cam.get_intrinsics_as_msg()
                intrinsic_msg.header.stamp = now
                intrinsic_msg.header.frame_id = frame_id
                self.intr_pub[vdisp].publish( intrinsic_msg )

                translation, rotation = cam.get_ROS_tf()
                self.tf_b.sendTransform( translation,
                                         rotation,
                                         future,
                                         frame_id,
                                         '/map',
                                         )

                r = flyvr.rviz_utils.get_frustum_markers( cam, id_base=cam_id_base, scale=1.0, stamp=now )
                self.frustum_pub[vdisp].publish(r['markers'])

    def on_trigger_cal(self, widget, path):
        vdisp = self.vdisp_store[path][0]

        method = self._ui.get_object('cal_method_cbtext').get_active_text()
        self.launch_calibration( method, vdisp )

    def launch_calibration(self, method, vdisp ):
        orig_data = []
        for row in self.point_store:
            if row[VDISP]==vdisp:
                orig_data.append( [ row[TEXU], row[TEXV], row[DISPLAYX], row[DISPLAYY] ] )
        orig_data = np.array(orig_data)
        uv = orig_data[:,:2]
        XYZ = self.geom.model.texcoord2worldcoord(uv)
        xy = orig_data[:,2:4]

        if method in ('DLT','RANSAC DLT'):
            ransac = method.startswith('RANSAC')
            r = dlt.dlt(XYZ, xy, ransac=ransac )
            c1 = camera_model.load_camera_from_pmat( r['pmat'],
                                                     width=self.dsc.width,
                                                     height=self.dsc.height,
                                                     )

            if 0:
                c2 = c1.get_flipped_camera()

                # slightly hacky way to find best camera direction
                obj = self.geom.model.get_center()

                d1 = np.sqrt( np.sum( (c1.get_lookat() - obj)**2 ))
                d2 = np.sqrt( np.sum( (c2.get_lookat() - obj)**2 ))
                if d1 < d2:
                    #print 'using normal camera'
                    camera = c1
                else:
                    print 'using flipped camera'
                    camera = c2
            elif 1:
                farr = self.geom.compute_for_camera_view( c1,
                                                          what='texture_coords' )

                u = farr[:,:,0]
                good = ~np.isnan( u )
                npix=np.sum( np.nonzero( good ) )
                if npix==0:
                    print 'using flipped camera, otherwise npix = 0'
                    camera = c1.get_flipped_camera()
                else:
                    camera = c1
            else:
                camera = c1
        elif method in ['extrinsic only','iterative extrinsic only']:
            assert self.display_intrinsic_cam is not None, 'need intrinsic calibration'

            di = self.dsc.get_display_info()

            mirror = None
            if 'virtualDisplays' in di:
                found = False
                for d in di['virtualDisplays']:
                    if d['id'] == vdisp:
                        found = True
                        mirror = d.get('mirror',None)
                        break
                assert found


            if mirror is not None:
                cami = self.display_intrinsic_cam.get_mirror_camera(axis=mirror)
            else:
                cami = self.display_intrinsic_cam

            if method == 'iterative extrinsic only':
                result = fit_extrinsics_iterative(cami,XYZ,xy)
            else:
                result = fit_extrinsics(cami,XYZ,xy)

            c1 = result['cam']
            if 1:
                farr = self.geom.compute_for_camera_view( c1,
                                                          what='texture_coords' )

                u = farr[:,:,0]
                good = ~np.isnan( u )
                npix=np.sum( np.nonzero( good ) )
                if npix==0:
                    print 'using flipped camera, otherwise npix = 0'
                    camera = c1.get_flipped_camera()
                else:
                    camera = c1
            else:
                camera = c1
            del result
        else:
            raise ValueError('unknown calibration method %r'%method)

        projected_points = camera.project_3d_to_pixel( XYZ )
        reproj_error = np.sum( (projected_points - xy)**2, axis=1)
        mre = np.mean(reproj_error)

        for row in self.vdisp_store:
            if row[VS_VDISP]==vdisp:
                row[VS_MRE] = mre
                row[VS_CAMERA_OBJECT] = camera
        self.update_bg_image()

    def update_bg_image(self):
        arr = np.zeros( (self.dsc.height,self.dsc.width,3), dtype=np.uint8 )

        # draw beachballs ----------------------

        # FIXME: add masks

        for row in self.vdisp_store:
            if not row[VS_SHOW_BEACHBALL]:
                continue
            cam = row[VS_CAMERA_OBJECT]
            if cam is None:
                continue

            farr = self.geom.compute_for_camera_view( cam,
                                                      what='texture_coords' )

            u = farr[:,:,0]
            good = ~np.isnan( u )
            print 'showing beachball for %r'%row[VS_VDISP]
            print '  npix0',np.sum( np.nonzero( good ) )

            arr2 = simple_geom.tcs_to_beachball(farr)
            print '  npix1',np.sum(np.nonzero(arr2))
            arr = arr+arr2 # FIXME: hacky OR operation assumes pixels are either 0 or final value

            print '  npix2',np.sum(np.nonzero(arr))

        # draw individual points ---------------

        showing = []
        for row in self.point_store:
            if not row[SHOWPT]:
                continue
            xf,yf = row[DISPLAYX], row[DISPLAYY]
            try:
                x = int(np.round(xf))
                y = int(np.round(yf))
            except ValueError:
                # Likely cannot convert nan to integer.
                continue
            if 0 <= x and x < self.dsc.width:
                if 0 <= y and y < self.dsc.height:
                    arr[y,x] = 255
                    showing.append( (x,y) )
        self.dsc.show_pixels(arr)
        return arr

if __name__ == "__main__":
    rospy.init_node("extrinsic_wizard")
    rosgobject.get_ros_thread() #ensure ros is spinning
    rosgobject.add_console_logger()


    parser = argparse.ArgumentParser()

    parser.add_argument('--geom_fname', type=str, required=True,
                        help='filename  (.json) specifying display geometry')

    parser.add_argument('--display_server', type=str,
                        required=True,
                        help='the path of the display server to configure')

    argv = rospy.myargv()
    args = parser.parse_args(argv[1:])

    if 0:
        dsc = display_client.DisplayServerProxy(args.display_server,
                                                wait=True)
    else:
        import hack_pinhole
        import yaml
        yaml_fname = 'andrew4k.yaml'
        data = yaml.load( open(yaml_fname).read() )

        dsc = hack_pinhole.MockDisplayClient(data['display'])

    geom = simple_geom.Geometry(filename=args.geom_fname)

    u = UI(dsc, geom)

    Gtk.main()