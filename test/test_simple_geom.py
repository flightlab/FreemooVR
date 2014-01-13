import numpy as np
import yaml

# ROS imports
import roslib; roslib.load_manifest('flyvr')
import flyvr.simple_geom as simple_geom
import pymvg

def get_sample_camera():
    yaml_str = """header:
  seq: 0
  stamp:
    secs: 0
    nsecs: 0
  frame_id: ''
height: 494
width: 659
distortion_model: plumb_bob
D:
  - -0.331416226762
  - 0.143584747016
  - 0.00314558656669
  - -0.00393597842852
  - 0.0
K:
  - 516.385667641
  - 0.0
  - 339.167079537
  - 0.0
  - 516.125799368
  - 227.379935241
  - 0.0
  - 0.0
  - 1.0
R:
  - 1.0
  - 0.0
  - 0.0
  - 0.0
  - 1.0
  - 0.0
  - 0.0
  - 0.0
  - 1.0
P:
  - 444.369750977
  - 0.0
  - 337.107817516
  - 0.0
  - 0.0
  - 474.186859131
  - 225.062742824
  - 0.0
  - 0.0
  - 0.0
  - 1.0
  - 0.0
binning_x: 0
binning_y: 0
roi:
  x_offset: 0
  y_offset: 0
  height: 0
  width: 0
  do_rectify: False"""
    d = yaml.load(yaml_str)
    cam1 = pymvg.CameraModel.from_dict(d,extrinsics_required=False)

    eye = (10,20,30)
    lookat = (11,20,30)
    up = (0,-1,0)
    cam = cam1.get_view_camera(eye, lookat, up)

    return cam

def nan_shape_allclose( a,b, **kwargs):
    if a.shape != b.shape:
        return False
    good_a = ~np.isnan(a)
    good_b = ~np.isnan(b)
    if not np.alltrue( good_a == good_b):
        return False
    aa = a[good_a]
    bb = b[good_b]
    return np.allclose( aa, bb, **kwargs)

def test_worldcoord_roundtrip():

    # PlanarRectangle
    ll = {'x':0, 'y':0, 'z':0}
    lr = {'x':1, 'y':0, 'z':0}
    ul = {'x':0, 'y':1, 'z':0}

    # Cylinder
    base = {'x':0, 'y':0, 'z':0}
    axis = {'x':0, 'y':0, 'z':1}
    radius = 1

    # Sphere
    center = {'x':0, 'y':0, 'z':0}
    radius = 1

    inputs = [ (simple_geom.PlanarRectangle, dict(lowerleft=ll, upperleft=ul, lowerright=lr)),
               (simple_geom.Cylinder, dict(base=base, axis=axis, radius=radius)),
               (simple_geom.Sphere, dict(center=center, radius=radius)),
               ]
    for klass, kwargs in inputs:
        yield check_worldcoord_roundtrip, klass, kwargs

def check_worldcoord_roundtrip(klass,kwargs):
    model = klass(**kwargs)

    eps = 0.001 # avoid 0-2pi wrapping issues on sphere and cylinder
    tc1 = np.array( [[eps,eps],
                     [eps,1-eps],
                     [1-eps,1-eps],
                     [1-eps,eps],
                     [0.5,eps],
                     [eps, 0.5]] )
    wc1 = model.texcoord2worldcoord(tc1)
    tc2 = model.worldcoord2texcoord(wc1)
    wc2 = model.texcoord2worldcoord(tc2)
    assert nan_shape_allclose( tc1, tc2)
    assert nan_shape_allclose( wc1, wc2 )

def test_rect():
    ll = {'x':0, 'y':0, 'z':0}
    lr = {'x':1, 'y':0, 'z':0}
    ul = {'x':0, 'y':1, 'z':0}

    rect = simple_geom.PlanarRectangle(lowerleft=ll, upperleft=ul, lowerright=lr)

    zval = 20.0
    # several look-at locations
    b=np.array([(0,0,0),
                (0,1,0),
                (0,-1,zval),
                (0,0.5,0),
                (0,0,1),
                (0,0,2*zval),
                (0,0,1.0001*zval),
                ])

    a=np.zeros( b.shape )
    a[:,2] = zval

    actual = rect.get_first_surface(a,b)
    expected = np.array([(0,0,0),
                         (0,1,0),
                         (np.nan,np.nan,np.nan),
                         (0,0.5,0),
                         (0,0,0),
                         (np.nan,np.nan,np.nan),
                         (np.nan,np.nan,np.nan),
                         ])

    assert nan_shape_allclose( actual, expected)

    actual_norms = rect.worldcoord2normal( actual )
    expected_norms = np.array([(0,0,1),
                               (0,0,1),
                               (np.nan,np.nan,np.nan),
                               (0,0,1),
                               (0,0,1),
                               (np.nan,np.nan,np.nan),
                               (np.nan,np.nan,np.nan),
                               ])
    assert nan_shape_allclose( actual_norms, expected_norms)

def test_cyl():
    base = {'x':0, 'y':0, 'z':0}
    axis = {'x':0, 'y':0, 'z':1}
    radius = 1
    cyl = simple_geom.Cylinder(base=base, axis=axis, radius=radius)

    # several look-at locations
    b=np.array([(0,0,0),
                (0,1,0),
                (0,-1,0),
                (0,0,10),
                (0,0,-10),
                ])

    # the look-from location is essentially (+inf,0,0)
    a=np.zeros( b.shape )
    a[:,0] = 1e10

    actual = cyl.get_first_surface(a,b)
    expected = np.array([(1,0,0),
                         (0,1,0),
                         (0,-1,0),
                         (np.nan,np.nan,np.nan),
                         (np.nan,np.nan,np.nan),
                         ])
    assert nan_shape_allclose( actual, expected)

    actual_norms = cyl.worldcoord2normal( actual )
    expected_norms = expected
    assert nan_shape_allclose( actual_norms, expected_norms)


def test_sphere():
    center = {'x':0, 'y':0, 'z':0}
    radius = 1
    sphere = simple_geom.Sphere(center=center, radius=radius)

    # several look-at locations
    b=np.array([
                (0,1,0),
                (0,2,0),
                ])

    # the look-from location is essentially (+inf,0,0)
    a=np.zeros( b.shape )
    a[:,0] = 10
    a[:,1] = 1

    actual = sphere.get_first_surface(a,b)
    expected = np.array([
                         (0,1,0),
                         (np.nan, np.nan, np.nan),
                         ])
    assert nan_shape_allclose( actual, expected)

def test_sphere2():
    center = {'x':0, 'y':0, 'z':0}
    radius = 1
    sphere = simple_geom.Sphere(center=center, radius=radius)

    # several look-at locations
    b=np.array([(0,0,0),
                (0,1,0),
                (0,-1,0),
                (0,0,1),
                (0,0,10),
                (0,0,-10),
                ])

    # the look-from location is essentially (+inf,0,0)
    a=np.zeros( b.shape )
    a[:,0] = 1e10

    actual = sphere.get_first_surface(a,b)
    expected = np.array([(1,0,0),
                         (0,1,0),
                         (0,-1,0),
                         (0,0,1),
                         (np.nan,np.nan,np.nan),
                         (np.nan,np.nan,np.nan),
                         ])
    assert nan_shape_allclose( actual, expected)

    actual_tcs = sphere.worldcoord2texcoord( actual )
    expected_tcs = np.array([[0, 0.5],
                             [0.25, 0.5],
                             [0.75, 0.5],
                             [0.0, 1.0],
                             [np.nan, np.nan],
                             [np.nan, np.nan],
                             ])
    assert nan_shape_allclose( actual_tcs, expected_tcs)

    actual_norms = sphere.worldcoord2normal( actual )
    expected_norms = expected
    assert nan_shape_allclose( actual_norms, expected_norms)


def test_geom_class():
    cam = get_sample_camera()

    d = {'model':'cylinder',
         'base':{'x':0,'y':0,'z':0},
         'axis':{'x':0,'y':0,'z':1},
         'radius':1.0}
    geom = simple_geom.Geometry(geom_dict=d)
    wcs = geom.compute_for_camera_view(cam,'world_coords')
    tcs = geom.compute_for_camera_view(cam,'texture_coords')
    dist = geom.compute_for_camera_view(cam,'distance')
    angle = geom.compute_for_camera_view(cam,'incidence_angle')
